#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audited deletion of REGENERABLE paths (caches, build/test scratch).

The mechanism behind ``scitex-storage purge``.

WHY A DELETE VERB EXISTS AT ALL in a package whose rule is "move, don't
delete": ``reclaim`` moves a path aside and ``archive`` copies it off-host,
and both are right for data whose value is unknown. Neither is right for a
uv cache, a merged worktree's ``.venv`` or a CI job's leftover ``_temp``. Their
recipe is the network or the lockfile, so moving them only relocates the
problem (often onto the same NVMe), and an operator facing a full root disk
ended up writing a one-off ``rm -rf`` loop with no record of what went. This
verb is that loop, made auditable and refusing:

* every path gets a JSON audit record, written BEFORE the first unlink
  (``deleting``) and again after (``deleted`` / ``failed``), so an
  interrupted run still says what it was doing;
* a path a live process holds open, maps, or uses as its cwd/root is
  REFUSED. The ``/proc`` scan carries a positive control (a file this
  process holds open must be seen), so a blind scan cannot read as "unused";
* a path must be REGENERABLE: ``find-recipe`` says ``cache`` or
  ``regenerable``, or the operator states WHY it is regenerable with
  ``reason`` (for example "CI job temp, recreated per job"). The reason is
  stored in the record, so an operator's judgement is attributable rather
  than silent;
* mount points, and trees that contain a mount point, are refused: a
  recursive delete must never cross into another filesystem;
* ``min_age_days`` refuses a tree whose NEWEST entry is younger than the
  threshold, which is how "leftovers older than three days" is enforced
  mechanically rather than by eye.

Dry-run is the default at the CLI; a plan writes nothing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from socket import gethostname

from .._measure._open_handles import holders_under, iter_open_paths
from .._measure._regenerable import CACHE, REGENERABLE, is_regenerable
from ._durable import atomic_json

_LOG_SUBDIR = "~/.scitex/scitex-storage/runtime/purge-logs"
SCHEMA = "scitex-storage.purge-log/v1"

ELIGIBLE = "eligible"
REFUSED = "refused"


def log_dir() -> Path:
    """Where purge audit records live (resolved per call, so HOME can be sandboxed)."""
    return Path(_LOG_SUBDIR).expanduser()


@dataclass
class PurgeEntry:
    """One path's plan: what it is, and whether it may be deleted."""

    path: str
    decision: str  # ELIGIBLE or REFUSED
    size_bytes: int = 0
    file_count: int = 0
    newest_mtime: float | None = None
    recipe_verdict: str | None = None
    recipe_evidence: str | None = None
    reason: str | None = None
    refusal: str | None = None


@dataclass
class PurgePlan:
    run_id: str
    reason: str | None
    min_age_days: float | None
    entries: list[PurgeEntry] = field(default_factory=list)

    @property
    def eligible(self) -> list[PurgeEntry]:
        return [e for e in self.entries if e.decision == ELIGIBLE]

    @property
    def eligible_bytes(self) -> int:
        return sum(e.size_bytes for e in self.eligible)


def _measure(path: Path) -> tuple[int, int, float | None, str | None]:
    """(allocated bytes, entries, newest mtime, nested mount or None).

    Allocated bytes (``st_blocks``), not apparent size: what a purge frees is
    blocks. Symlinks are never followed. A subdirectory on another device is
    returned as the nested mount so the caller can refuse the whole tree.
    """
    st = path.lstat()
    if not path.is_dir() or path.is_symlink():
        return st.st_blocks * 512, 1, st.st_mtime, None
    root_dev = st.st_dev
    total, count, newest = st.st_blocks * 512, 1, st.st_mtime
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs:
            sub = os.path.join(root, name)
            try:
                sst = os.lstat(sub)
            except OSError:
                continue
            if sst.st_dev != root_dev:
                return total, count, newest, sub
            total += sst.st_blocks * 512
            count += 1
            newest = max(newest, sst.st_mtime)
        for name in files:
            try:
                fst = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += fst.st_blocks * 512
            count += 1
            newest = max(newest, fst.st_mtime)
    return total, count, newest, None


def _protected(path: Path) -> str | None:
    """A refusal message for a path no purge may ever take, else None."""
    home = Path(os.path.expanduser("~")).resolve()
    if path == Path("/") or path == home or path in home.parents:
        return f"protected path {path} (root, HOME or an ancestor of HOME)"
    if os.path.ismount(path):
        return f"{path} is a mount point; purge never deletes a mount"
    return None


def _open_paths_with_control() -> list[str]:
    """Every observable open path, cwd and root, proven by a positive control.

    Raises RuntimeError when the control is not seen: a blind scan must stop
    the purge, never license it.
    """
    with tempfile.NamedTemporaryFile(prefix="scitex-storage-purge-control-") as ctl:
        observed = list(iter_open_paths())
        control = os.path.realpath(ctl.name)
    if control not in observed:
        raise RuntimeError(
            "open-handle scan is blind: it did not see a file this process "
            "holds open (positive control). Refusing to purge anything."
        )
    return observed


def plan_purge(
    paths: list[str | Path],
    *,
    run_id: str,
    reason: str | None = None,
    min_age_days: float | None = None,
    now: float | None = None,
    open_paths: list[str] | None = None,
) -> PurgePlan:
    """Decide, path by path, what may be deleted. Read-only.

    ``open_paths`` lets a test supply the observed holders; ``None`` scans
    ``/proc`` with a positive control.
    """
    now = time.time() if now is None else now
    observed = _open_paths_with_control() if open_paths is None else open_paths
    plan = PurgePlan(run_id=run_id, reason=reason, min_age_days=min_age_days)
    for raw in paths:
        plan.entries.append(
            _plan_one(Path(raw).expanduser(), reason, min_age_days, now, observed)
        )
    return plan


def _plan_one(source, reason, min_age_days, now, observed) -> PurgeEntry:
    if not source.exists() and not source.is_symlink():
        return PurgeEntry(str(source), REFUSED, refusal="path does not exist")
    source = Path(os.path.abspath(source))
    resolved = source if source.is_symlink() else source.resolve()
    blocked = _protected(resolved)
    if blocked:
        return PurgeEntry(str(source), REFUSED, refusal=blocked)
    size, count, newest, nested = _measure(source)
    entry = PurgeEntry(str(source), REFUSED, size, count, newest, reason=reason)
    if nested:
        entry.refusal = f"contains another filesystem's mount point: {nested}"
        return entry
    holders = holders_under(str(resolved), observed)
    if holders:
        more = f" (+{len(holders) - 3} more)" if len(holders) > 3 else ""
        entry.refusal = f"held by a live process: {', '.join(holders[:3])}{more}"
        return entry
    if min_age_days is not None and newest is not None:
        age_days = (now - newest) / 86400
        if age_days < min_age_days:
            entry.refusal = (
                f"newest entry is {age_days:.1f} days old, younger than "
                f"--min-age-days {min_age_days:g}"
            )
            return entry
    if source.is_dir() and not source.is_symlink():
        verdict = is_regenerable(str(source))
        entry.recipe_verdict = verdict.verdict
        entry.recipe_evidence = verdict.evidence
        if verdict.verdict not in (CACHE, REGENERABLE) and not reason:
            entry.refusal = (
                f"find-recipe says {verdict.verdict!r}; state why it is "
                "regenerable with --reason to purge it"
            )
            return entry
    elif not reason:
        entry.refusal = "a file has no recipe verdict; state why with --reason"
        return entry
    entry.decision = ELIGIBLE
    return entry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_purge(plan: PurgePlan) -> list[dict]:
    """Delete every ELIGIBLE entry, writing an audit record around each.

    Refused entries are recorded too, so the log answers "what did this run
    decline, and why" as well as "what did it delete".
    """
    run_dir = log_dir() / plan.run_id
    records = []
    for index, entry in enumerate(plan.entries):
        record_path = run_dir / f"{index:05d}.json"
        record = {
            "schema": SCHEMA,
            "run_id": plan.run_id,
            "host": gethostname(),
            "user": os.environ.get("USER"),
            "min_age_days": plan.min_age_days,
            **asdict(entry),
            "started_at": _now_iso(),
        }
        if entry.decision != ELIGIBLE:
            record["status"] = "refused"
            atomic_json(record_path, record)
            records.append(record)
            continue
        record["status"] = "deleting"
        atomic_json(record_path, record)
        errors = _delete(Path(entry.path))
        record["status"] = "failed" if errors else "deleted"
        if errors:
            record["errors"] = errors[:20]
        record["finished_at"] = _now_iso()
        atomic_json(record_path, record)
        records.append(record)
    return records


def _delete(path: Path) -> list[str]:
    """Remove ``path`` without following symlinks; return error strings."""
    errors: list[str] = []
    if path.is_symlink() or not path.is_dir():
        try:
            path.unlink()
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror}")
        return errors

    def _onexc(_func, target, exc):
        errors.append(f"{target}: {getattr(exc, 'strerror', exc)}")

    if _supports_onexc():
        shutil.rmtree(path, onexc=_onexc)
    else:  # Python < 3.12
        shutil.rmtree(path, onerror=lambda f, p, info: _onexc(f, p, info[1]))
    return errors


def _supports_onexc() -> bool:
    import inspect

    return "onexc" in inspect.signature(shutil.rmtree).parameters


def list_runs() -> list[dict]:
    """Summaries of recorded purge runs, newest first."""
    root = log_dir()
    if not root.is_dir():
        return []
    import json

    out = []
    for run in sorted(root.iterdir(), reverse=True):
        counts: dict[str, int] = {}
        freed = 0
        for rec in run.glob("*.json"):
            try:
                data = json.loads(rec.read_text())
            except (OSError, ValueError):
                continue
            counts[data.get("status", "?")] = counts.get(data.get("status", "?"), 0) + 1
            if data.get("status") == "deleted":
                freed += int(data.get("size_bytes") or 0)
        out.append({"run_id": run.name, "counts": counts, "bytes_freed": freed})
    return out


# EOF
