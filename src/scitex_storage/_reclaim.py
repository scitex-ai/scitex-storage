#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reversible local reclaim: move a path aside instead of deleting it.

The mechanism behind ``scitex-storage reclaim``. It implements the
operator's archive-instead-of-delete rule — 「アーカイブというディレクトリ
を一時的に作ってそちらに紛らわしいものは入れておく」 — as a plain, local,
**reversible move**: ``reclaim PATH`` relocates PATH into an archive
directory and records exactly where it came from, so it can be moved back.

WHY THIS EXISTS, and why reversibility is the whole design rather than a
nicety: a cleanup tool that DELETES needs a near-perfect classifier, because
a wrong call is unrecoverable — so the classifier becomes load-bearing, has
to be certain, and therefore never ships. A tool that MOVES-ASIDE needs only
a rough call, because a wrong one costs a move back. So the classifier can be
80% right and still safe, and 80% right ships. Reversibility is what lets
the hard part (deciding what is disposable) be rough, and this module is that
reversibility, kept deliberately separate from any classifier. What gets
pulled back out of the archive is the classifier's error rate, measured
rather than guessed (see :func:`restore_reclaim` and the ``restored`` flag).

DESTINATION IS A PARAMETER, and it is the one real decision here:

* DEFAULT — an adjacent ``.old/<timestamp>/`` beside each source (the
  constitution's "archive superseded files to ``.old/<timestamp>/`` instead
  of deleting"). Same filesystem, so the move is an atomic ``os.rename``:
  instant regardless of size, and it cannot half-complete. This serves the
  *migration/reorg* use — move the ambiguous mass aside, test the new shape
  on a clean slate — but note it does NOT reduce the source filesystem's
  inode or space usage: the files are still there, just relocated.
* OVERRIDE (``archive_root``) — any directory, including one on a DIFFERENT
  filesystem with headroom. That IS inode/space relief (the files leave the
  full filesystem), at the cost of atomicity: a cross-filesystem move is a
  copy-then-delete (``shutil.move``), which can be interrupted, so it is
  verified before the source is removed.

So one mechanism serves both jobs; WHERE you point it decides which. The
verb never guesses the destination — the caller states it, because "free
this filesystem" and "tidy this directory" are different intents that must
not be conflated (a lesson this package learned the hard way elsewhere).

NOT in scope here, deliberately: the *classifier* (which paths are
disposable) is a separate, advisory concern — this module moves exactly the
paths it is handed, nothing inferred. And DELETE is a separate, later verb
against things that have already sat archived and unmissed; this module only
ever moves, never unlinks user data. The archive is the safety buffer
between "moved aside" and "gone", and collapsing the two would remove the
only thing that makes a rough decision safe.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Where reclaim manifests live. Resolved per-call (not a module constant) so
#: a test can sandbox it via ``$HOME`` — matching ``_archive.py``'s pattern.
_MANIFEST_SUBDIR = "~/.scitex/scitex-storage/runtime/reclaim-manifests"

#: The adjacent-archive directory name (the constitution's ``.old``).
_ADJACENT_ARCHIVE_DIRNAME = ".old"


def _manifest_dir() -> Path:
    return Path(_MANIFEST_SUBDIR).expanduser()


@dataclass
class ReclaimEntry:
    """One path moved by a reclaim run: where it was, where it went."""

    original: str  # absolute source path, as it was before the move
    archived: str  # absolute path it now lives at inside the archive
    size_bytes: int
    file_count: int


@dataclass
class ReclaimPlan:
    """What a reclaim run WOULD move — computed, never executed."""

    run_id: str
    archive_root: str | None  # None => adjacent `.old/<run_id>/` per source
    entries: list[ReclaimEntry] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(e.size_bytes for e in self.entries)

    @property
    def total_files(self) -> int:
        return sum(e.file_count for e in self.entries)


@dataclass
class ReclaimManifest:
    """Persisted record of a completed reclaim run — the source of truth for
    restore, and the substrate for the restore-rate accuracy metric."""

    run_id: str
    reclaimed_at: float
    archive_root: str | None
    entries: list[ReclaimEntry]
    restored: bool = False  # flipped true by restore_reclaim (the metric)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ReclaimManifest:
        entries = [ReclaimEntry(**e) for e in data.get("entries", [])]
        return cls(
            run_id=data["run_id"],
            reclaimed_at=data["reclaimed_at"],
            archive_root=data.get("archive_root"),
            entries=entries,
            restored=data.get("restored", False),
        )


def _measure(path: Path) -> tuple[int, int]:
    """Return (total_size_bytes, file_count) for ``path``.

    A file counts as itself; a directory is walked (stat-only, symlinks never
    followed — a symlink is one inode, its target is not ours to count). Kept
    local and dependency-free: reclaim must work where ``fd`` is absent, and
    a reclaim target is being moved, not scanned at scale.
    """
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size, 1
        except OSError:
            return 0, 1
    total = 0
    count = 0
    for root, dirs, files in os.walk(path):
        # Do not descend into symlinked dirs (os.walk already won't unless
        # followlinks=True, which we never pass) and count them as leaves.
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.lstat().st_size
            except OSError:
                pass
            count += 1
        for name in dirs:
            dp = Path(root) / name
            if dp.is_symlink():
                count += 1  # a symlinked dir is one inode, not a descent
    return total, count


def _archive_destination(source: Path, run_id: str, archive_root: str | None) -> Path:
    """Compute the archive path a ``source`` would move to.

    ``archive_root=None`` => adjacent ``<source.parent>/.old/<run_id>/<name>``
    (same filesystem, atomic). Otherwise ``<archive_root>/<run_id>/<name>``.
    The ``run_id`` layer groups one invocation's moves and prevents two
    sources with the same basename from colliding across runs.
    """
    if archive_root is None:
        base = source.parent / _ADJACENT_ARCHIVE_DIRNAME / run_id
    else:
        base = Path(archive_root).expanduser() / run_id
    return base / source.name


def plan_reclaim(
    paths: list[str | Path],
    *,
    run_id: str,
    archive_root: str | None = None,
) -> ReclaimPlan:
    """Compute (never execute) a reclaim plan for ``paths``.

    Read-only: stats each source, resolves where it would go, touches
    nothing. Fail-loud on a missing source or a source that is itself the
    archive area — a bad path is never silently skipped. ``run_id`` is
    supplied by the caller (a timestamp, typically) so the value is explicit
    and testable rather than wall-clock-dependent inside here.
    """
    plan = ReclaimPlan(run_id=run_id, archive_root=archive_root)
    for raw in paths:
        source = Path(raw).expanduser()
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(f"path does not exist: {source}")
        source = source.resolve() if not source.is_symlink() else source.absolute()
        if source.name == _ADJACENT_ARCHIVE_DIRNAME:
            raise ValueError(
                f"refusing to reclaim the archive directory itself: {source}"
            )
        dest = _archive_destination(source, run_id, archive_root)
        size, count = _measure(source)
        plan.entries.append(
            ReclaimEntry(
                original=str(source),
                archived=str(dest),
                size_bytes=size,
                file_count=count,
            )
        )
    return plan


def _same_filesystem(a: Path, b: Path) -> bool:
    """True when ``a`` and the nearest existing ancestor of ``b`` share a
    device.

    Used only to decide whether a move can be an atomic ``os.rename``; the
    destination usually does not exist yet, so its device is taken from the
    closest ancestor that does. A False result (or an un-stattable path)
    simply routes through the verified copy path, which is always correct,
    only slower — so this can be conservative without being wrong.
    """
    try:
        dev_a = os.lstat(a).st_dev
    except OSError:
        return False
    probe = b
    while not probe.exists():
        if probe.parent == probe:
            return False
        probe = probe.parent
    try:
        return dev_a == probe.stat().st_dev
    except OSError:
        return False


def _move_one(entry: ReclaimEntry) -> None:
    """Move a single source to its archive destination, reversibly.

    Same filesystem -> atomic ``os.rename``. Cross filesystem -> ``shutil.move``
    (copy then delete), which is not atomic; the copy is left in place if it
    fails partway (the source is only removed by shutil AFTER a successful
    copy), so a crash never loses data, at worst duplicates it.
    """
    src = Path(entry.original)
    dst = Path(entry.archived)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _same_filesystem(src, dst.parent):
        os.rename(src, dst)
    else:
        shutil.move(str(src), str(dst))


def apply_reclaim(plan: ReclaimPlan) -> ReclaimManifest:
    """Execute a reclaim plan: move every entry, then write the manifest.

    The manifest is written LAST, after all moves succeed, and records the
    exact original->archived mapping so :func:`restore_reclaim` is precise.
    A move that raises aborts the run and propagates — entries already moved
    stay in the archive (recoverable), and no manifest is written for a
    partial run, so a partial run is visible as "files in .old with no
    manifest" rather than silently half-recorded.
    """
    for entry in plan.entries:
        _move_one(entry)
    manifest = ReclaimManifest(
        run_id=plan.run_id,
        reclaimed_at=_now(),
        archive_root=plan.archive_root,
        entries=list(plan.entries),
    )
    _write_manifest(manifest)
    return manifest


def _now() -> float:
    import time

    return time.time()


def _manifest_path(run_id: str) -> Path:
    return _manifest_dir() / f"{run_id}.json"


def _write_manifest(manifest: ReclaimManifest) -> None:
    path = _manifest_path(manifest.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_dict(), indent=2))


def load_manifest(run_id: str) -> ReclaimManifest:
    """Load a run's manifest. Raises FileNotFoundError if the run is unknown."""
    path = _manifest_path(run_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no reclaim manifest for run {run_id!r} at {path} "
            "-- was anything reclaimed under this run id?"
        )
    return ReclaimManifest.from_dict(json.loads(path.read_text()))


def list_manifests() -> list[ReclaimManifest]:
    """Every recorded reclaim run, newest first. The restore-rate substrate."""
    d = _manifest_dir()
    if not d.is_dir():
        return []
    out: list[ReclaimManifest] = []
    for p in d.glob("*.json"):
        try:
            out.append(ReclaimManifest.from_dict(json.loads(p.read_text())))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda m: m.reclaimed_at, reverse=True)


def restore_reclaim(run_id: str) -> ReclaimManifest:
    """Move a run's archived entries back to their original locations.

    This is the reversal the whole design turns on, and the measurement: it
    flips the manifest's ``restored`` flag, so the fraction of runs restored
    is the classifier's error rate, recorded rather than estimated (see
    :func:`restore_rate`). Fail-loud if an original path is now occupied — a
    restore must never overwrite something that took the vacated spot.
    """
    manifest = load_manifest(run_id)
    for entry in manifest.entries:
        src = Path(entry.archived)
        dst = Path(entry.original)
        if not src.exists() and not src.is_symlink():
            raise FileNotFoundError(
                f"archived copy missing, cannot restore: {src} "
                "(was it deleted or moved out of the archive?)"
            )
        if dst.exists() or dst.is_symlink():
            raise FileExistsError(
                f"refusing to restore over an existing path: {dst} "
                "(something occupied the original location since reclaim)"
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        if _same_filesystem(src, dst.parent):
            os.rename(src, dst)
        else:
            shutil.move(str(src), str(dst))
    manifest.restored = True
    _write_manifest(manifest)
    return manifest


def restore_rate() -> float | None:
    """Fraction of recorded reclaim runs that were later restored.

    THE accuracy metric for whatever decided what to reclaim: a high rate
    means the decisions were wrong often and got pulled back; a low rate
    means the archived things really were disposable. ``None`` when nothing
    has been reclaimed yet (no denominator — reported as "no data", never as
    a reassuring 0.0, matching this package's could-not-look discipline).
    """
    manifests = list_manifests()
    if not manifests:
        return None
    restored = sum(1 for m in manifests if m.restored)
    return restored / len(manifests)


# EOF
