"""Crash-recoverable local quarantine after verified archive moves.

Sources remain in their original filesystem for seven days by default. State
transitions serialize through a process lock; durable intents precede renames.
A pending transition is recoverable, never eligible for automatic deletion.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._content_verify import DigestCancelled, digest_file, digest_tree, tree_digest
from ._durable import atomic_json, file_lock, rename_noreplace, sync_directory

STAGING_DIRNAME = ".stx-restorable"
MARKER_FILENAME = ".stx-restorable-record.json"
DEFAULT_RETENTION_DAYS = 7.0


@dataclass(frozen=True)
class StagedRecord:
    staged_path: str
    original_path: str
    staged_at: float
    delete_after: float
    content_digest: str = ""
    size_bytes: int = 0
    state: str = "staged"


@dataclass
class SweepReport:
    removed: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def _registry_path() -> Path:
    return Path(
        "~/.scitex/scitex-storage/runtime/restorable-registry.json"
    ).expanduser()


def _read_registry() -> list[dict]:
    path = _registry_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ValueError("Invalid retention registry; refusing to overwrite it")
    return data


def _write_registry(entries: list[dict]) -> None:
    atomic_json(_registry_path(), entries)


def _lock_path() -> Path:
    return _registry_path().with_suffix(".lock")


def _snapshot(path: Path, *, cancelled=None) -> tuple[str, int]:
    if cancelled is not None and cancelled():
        raise DigestCancelled("Retention verification cancelled")
    if path.is_symlink() or path.is_file():
        return digest_file(str(path), cancelled=cancelled), path.lstat().st_size
    manifest = digest_tree(str(path), cancelled=cancelled)
    if not manifest.usable or manifest.unreadable:
        raise ValueError("Cannot completely verify staged content")
    return tree_digest(manifest), sum(
        (path / name).lstat().st_size for name in manifest.digests
    )


def _marker_path_for(staged: Path) -> Path:
    # New markers are siblings: they never alter the archived content tree.
    return staged.parent / (staged.name + "." + MARKER_FILENAME)


def _contained_record(entry: dict) -> tuple[Path, Path]:
    staged = Path(entry.get("staged_path", ""))
    original = Path(entry.get("original_path", ""))
    if not staged.is_absolute() or not original.is_absolute():
        raise ValueError("Retention paths must be absolute")
    if staged.parent.name != STAGING_DIRNAME or staged.parent.is_symlink():
        raise ValueError("Invalid staging directory")
    if staged.parent.resolve().parent != original.parent.resolve():
        raise ValueError("Staging must remain next to its original")
    if type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0:
        raise ValueError("Invalid retained size")
    if (
        not isinstance(entry.get("content_digest"), str)
        or len(entry["content_digest"]) != 64
    ):
        raise ValueError("Missing retained content checksum")
    if not all(
        character in "0123456789abcdef" for character in entry["content_digest"]
    ):
        raise ValueError("Invalid retained checksum")
    if not all(
        isinstance(entry.get(key), (int, float)) and math.isfinite(entry[key])
        for key in ("staged_at", "delete_after")
    ):
        raise ValueError("Invalid retention timestamps")
    if entry["delete_after"] < entry["staged_at"]:
        raise ValueError("Invalid retention interval")
    return staged, original


def _marker_matches(staged: Path, entry: dict, *, allow_pending=False) -> bool:
    marker = _marker_path_for(staged)
    if marker.is_symlink():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not entry.get("content_digest"):
        return False
    if data == entry:
        return True
    return (
        allow_pending
        and data.get("state") in {"pending", "staged", "restoring"}
        and {
            key: value
            for key, value in data.items()
            if key not in {"state", "staged_at", "delete_after"}
        }
        == {
            key: value
            for key, value in entry.items()
            if key not in {"state", "staged_at", "delete_after"}
        }
    )


def stage_for_retention(
    source: str | Path,
    *,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    expected_digest: str | None = None,
    cancelled=None,
) -> StagedRecord:
    """Write a durable intent, rename without overwrite, and commit the record.

    On an ordinary failure after the rename, try to return the original to its
    path. A crash or rollback collision leaves a pending record with a known
    checksum; ``restore_staged`` can recover it, while sweeps refuse it.
    """
    if not math.isfinite(retention_days) or retention_days < 0:
        raise ValueError("Retention days must be finite and nonnegative")
    source = Path(source).absolute()
    with file_lock(_lock_path()):
        entries = _read_registry()
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(f"Nothing to stage: {source}")
        checksum, size = _snapshot(source, cancelled=cancelled)
        if expected_digest is not None and checksum != expected_digest:
            raise ValueError("Source changed before staging")
        staging = source.parent / STAGING_DIRNAME
        if staging.is_symlink():
            raise ValueError("Staging directory cannot be a symlink")
        staging.mkdir(exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        target = staging / f"{stamp}-{uuid.uuid4().hex}--{source.name}"
        staged_at = time.time()
        record = StagedRecord(
            str(target),
            str(source),
            staged_at,
            staged_at + retention_days * 86400,
            checksum,
            size,
            "pending",
        )
        intent = asdict(record)
        marker = _marker_path_for(target)
        atomic_json(marker, intent)
        try:
            _write_registry([*entries, intent])
        except BaseException:
            marker.unlink(missing_ok=True)
            raise
        moved = False
        try:
            rename_noreplace(source, target)
            moved = True
            sync_directory(source.parent)
            sync_directory(target.parent)
            if _snapshot(target, cancelled=cancelled) != (checksum, size):
                raise ValueError("Source changed during staging")
            committed_at = time.time()
            committed = dict(
                intent,
                state="staged",
                staged_at=committed_at,
                delete_after=committed_at + retention_days * 86400,
            )
            atomic_json(marker, committed)
            _write_registry([*entries, committed])
            return StagedRecord(**committed)
        except BaseException:
            # Do not overwrite a newly created original when rolling back.
            if moved:
                rename_noreplace(target, source)
                sync_directory(source.parent)
                sync_directory(target.parent)
            _write_registry(entries)
            marker.unlink(missing_ok=True)
            raise


def restore_staged(staged_path: str | Path) -> Path:
    """Restore only a registered, checksum-matching staged tree without overwrite.

    Pending crash intents are eligible for recovery, never for cleanup. A
    collision leaves both paths untouched. Registry write failure after the
    rename leaves the restored original and a recoverable intent on disk.
    """
    wanted = str(Path(staged_path).absolute())
    with file_lock(_lock_path()):
        entries = _read_registry()
        matches = [entry for entry in entries if entry.get("staged_path") == wanted]
        if len(matches) != 1:
            raise ValueError("Staged path is not uniquely registered")
        entry = matches[0]
        staged, original = _contained_record(entry)
        if not _marker_matches(staged, entry, allow_pending=True):
            raise ValueError("Staging marker does not match its record")
        if _snapshot(staged) != (entry["content_digest"], entry["size_bytes"]):
            raise ValueError("Staged content changed; refusing automatic restore")
        if original.exists() or original.is_symlink():
            raise FileExistsError(str(original))
        intent = dict(entry, state="restoring")
        atomic_json(_marker_path_for(staged), intent)
        _write_registry([intent if row is entry else row for row in entries])
        rename_noreplace(staged, original)
        sync_directory(original.parent)
        sync_directory(staged.parent)
        _write_registry([row for row in entries if row is not entry])
        _marker_path_for(staged).unlink(missing_ok=True)
        return original


def sweep_restorable(
    *,
    dry_run: bool = True,
    now: float | None = None,
    max_items: int = 32,
    max_bytes: int = 1024**3,
    max_seconds: float = 30.0,
    max_entries: int = 4096,
    cancelled=None,
) -> SweepReport:
    """Delete only expired committed records with matching markers and hashes.

    Defaults cap one pass at 32 records/1GiB/30 seconds and 4096 filesystem
    entries per tree. Larger trees require an explicitly larger budget.
    Hash cancellation is
    cooperative between directory entries and 1MiB reads; blocking filesystem
    syscalls remain an OS boundary. A dry run does not write records or locks.
    Legacy records without content baselines are refused, never upgraded into
    deletion permission. No periodic job is enabled by this API.
    """
    if (
        max_items < 1
        or max_bytes < 1
        or max_entries < 1
        or not math.isfinite(max_seconds)
        or max_seconds <= 0
    ):
        raise ValueError("Sweep budgets must be positive and finite")
    moment = time.time() if now is None else now
    deadline = time.monotonic() + max_seconds

    def stopped():
        return time.monotonic() >= deadline or bool(cancelled and cancelled())

    report = SweepReport()
    # A true dry run must not create a lock or registry, even on first use.
    if dry_run:
        return _sweep(
            _read_registry(),
            report,
            moment,
            max_items,
            max_bytes,
            max_entries,
            stopped,
            True,
        )
    with file_lock(_lock_path()):
        return _sweep(
            _read_registry(),
            report,
            moment,
            max_items,
            max_bytes,
            max_entries,
            stopped,
            False,
        )


def _sweep(
    entries, report, moment, max_items, max_bytes, max_entries, stopped, dry_run
):
    kept = []
    consumed = 0
    for index, entry in enumerate(entries):
        if index >= max_items or stopped():
            kept.extend(entries[index:])
            report.retained.extend(
                str(row.get("staged_path", "")) for row in entries[index:]
            )
            break
        try:
            staged, _ = _contained_record(entry)
            if not staged.exists() and not staged.is_symlink():
                report.pruned.append(str(staged))
                continue
            if entry.get("state") != "staged" or not _marker_matches(staged, entry):
                report.refused.append(str(staged))
            elif (
                moment < float(entry["delete_after"])
                or consumed + entry["size_bytes"] > max_bytes
            ):
                report.retained.append(str(staged))
            elif _bounded_snapshot(staged, max_entries, stopped) != (
                entry["content_digest"],
                entry["size_bytes"],
            ):
                report.refused.append(str(staged))
            else:
                consumed += entry["size_bytes"]
                if not dry_run:
                    if staged.is_symlink() or staged.is_file():
                        staged.unlink()
                    else:
                        shutil.rmtree(staged)
                    _marker_path_for(staged).unlink(missing_ok=True)
                    sync_directory(staged.parent)
                report.removed.append(str(staged))
                if not dry_run:
                    continue
        except _EntryBudgetExceeded:
            report.retained.append(str(staged))
        except DigestCancelled:
            kept.extend(entries[index:])
            report.retained.extend(
                str(row.get("staged_path", "")) for row in entries[index:]
            )
            break
        except (ValueError, KeyError, TypeError):
            report.refused.append(str(entry.get("staged_path", "")))
        except OSError as exc:
            report.errors[str(entry.get("staged_path", ""))] = str(exc)
        kept.append(entry)
    if not dry_run and kept != entries:
        _write_registry(kept)
    return report


class _EntryBudgetExceeded(RuntimeError):
    """The bounded pass cannot safely start a whole-tree removal."""


def _check_delete_budget(path, limit, stopped):
    count = 1
    if path.is_dir() and not path.is_symlink():

        def failed(error):
            raise error

        for _, directories, files in os.walk(path, followlinks=False, onerror=failed):
            if stopped():
                raise DigestCancelled("Retention cleanup cancelled")
            count += len(directories) + len(files)
            if count > limit:
                raise _EntryBudgetExceeded("Too many entries for one cleanup pass")
    if stopped():
        raise DigestCancelled("Retention cleanup cancelled")


def _bounded_snapshot(path, max_entries, stopped):
    _check_delete_budget(path, max_entries, stopped)
    return _snapshot(path, cancelled=stopped)
