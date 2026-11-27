#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_tier.py
"""Keep a hot volume below a fill level by moving its cold units to a colder host.

``tier ROOT --to HOST`` selects cold units with :func:`plan_tier` (see
``_tier_plan.py``) and moves them one at a time with :func:`apply_archive`:
copy, independent read-back, SHA256 of both sides, manifest, seven-day
restorable staging. Nothing here transfers or verifies data itself; every
byte that moves goes through the archive gate.

POINTERS. ``<path>.stx-tiered.json`` is written next to the unit only after
``apply_archive`` returned, which means the manifest records a positive
content verification and the source was staged. ``scitex-storage restore
<path>`` reads it, restores, re-verifies the content digest and only then
removes the pointer.

THE RUN LOG (``runtime/tier-logs/<run_id>.json``) is written before the first
mutation and rewritten after every unit: tiered, skipped with a reason, or
failed with the error. A crash mid-unit leaves the unit as ``archiving`` in
the log, and the archive manifest says how far it got.

FREED SPACE. The original stays in restorable staging on the same
filesystem until ``sweep-restorable --verify-remote --yes`` deletes it after
the retention period, and only once the remote copy re-hashes to the
recorded digest. The plan's projected usage is the usage after that sweep.
"""

from __future__ import annotations

import datetime
import json
import os
import stat
import time
import uuid
from pathlib import Path
from socket import gethostname

from ._archive import (
    SOURCE_KIND_DIRECTORY,
    apply_archive,
    apply_restore,
    plan_archive,
    plan_restore,
)
from ._archive_transport import _manifest_dir
from ._durable import atomic_json
from ._retention import DEFAULT_RETENTION_DAYS
from ._tier_plan import (
    DAY_SECONDS,
    DEFAULT_COLD_AFTER_DAYS,
    DEFAULT_MIN_SIZE_BYTES,
    POINTER_SUFFIX,
    TierPlan,
    TierUnit,
    holder_reason,
    plan_tier,
    pointer_path_for,
    walk_tree,
)

__all__ = [
    "DEFAULT_COLD_AFTER_DAYS",
    "DEFAULT_MIN_SIZE_BYTES",
    "POINTER_SCHEMA",
    "POINTER_SUFFIX",
    "RUN_LOG_SCHEMA",
    "TierPlan",
    "TierRefusedError",
    "TierUnit",
    "apply_tier",
    "plan_tier",
    "pointer_path_for",
    "read_pointer",
    "restore_tiered",
    "tier_log_dir",
]

POINTER_SCHEMA = "scitex-storage/tier-pointer/v1"
RUN_LOG_SCHEMA = "scitex-storage/tier-run/v1"


class TierRefusedError(RuntimeError):
    """The run could not start safely; nothing was moved."""


def tier_log_dir() -> Path:
    """Where tier run logs live (resolved per call, like manifests)."""
    return _manifest_dir().parent / "tier-logs"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tool_version() -> str:
    try:
        from .. import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 -- a version string must never stop a run
        return "unknown"


def _recheck(unit: TierUnit, root_device: int, cutoff: float, proc: Path) -> str:
    """Re-measure one unit right before it moves ('' means still eligible)."""
    path = Path(unit.path)
    if pointer_path_for(path).exists():
        return "a tier pointer exists"
    try:
        info = path.lstat()
    except OSError as exc:
        return f"gone since planning: {exc}"
    is_dir = stat.S_ISDIR(info.st_mode)
    expected_dir = unit.kind == SOURCE_KIND_DIRECTORY
    if is_dir != expected_dir or (not is_dir and not stat.S_ISREG(info.st_mode)):
        return "changed kind since planning"
    if info.st_dev != root_device:
        return "now on another filesystem"
    if is_dir:
        ctx = {"exclude": (), "cutoff": cutoff, "min_size": 0, "skipped": []}
        node = walk_tree(unit.path, "", root_device, ctx)
        if node.blocker:
            return f"can no longer move as a whole: {node.blocker}"
        newest = node.newest
    else:
        if info.st_nlink > 1:
            return "hard-linked (moving it frees nothing)"
        newest = max(info.st_atime, info.st_mtime)
    if newest >= cutoff:
        return "touched since planning (no longer cold)"
    reason, _ = holder_reason(unit.path, proc)
    return reason


def _write_pointer(unit: TierUnit, manifest, manifest_path: Path, run_id: str) -> Path:
    pointer = pointer_path_for(unit.path)
    atomic_json(
        pointer,
        {
            "schema": POINTER_SCHEMA,
            "original_path": unit.path,
            "kind": unit.kind,
            "destination": manifest.destination,
            "destination_tier": manifest.destination_tier,
            "remote_path": manifest.remote_path,
            "size_bytes": manifest.size_bytes,
            "file_count": manifest.file_count,
            "content_digest": manifest.content_digest,
            "verification_method": manifest.verification_method,
            "verified": manifest.verified,
            "manifest_path": str(manifest_path),
            "staged_path": manifest.staged_path,
            "tiered_at": _utc_now(),
            "tier_run_id": run_id,
            "tool": "scitex-storage",
            "tool_version": _tool_version(),
            "restore_hint": f"restore with: scitex-storage restore {unit.path} --yes",
        },
    )
    return pointer


def _new_run_log(plan: TierPlan) -> dict:
    run_id = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{uuid.uuid4().hex[:12]}"
    )
    return {
        "schema": RUN_LOG_SCHEMA,
        "run_id": run_id,
        "operation": "tier",
        "host": gethostname(),
        "uid": os.getuid(),
        "tool_version": _tool_version(),
        "started_at": _utc_now(),
        "status": "running",
        "log_path": str(tier_log_dir() / f"{run_id}.json"),
        "plan": plan.to_dict(),
        "units": [
            {
                "path": u.path,
                "kind": u.kind,
                "size_bytes": u.size_bytes,
                "status": "pending",
            }
            for u in plan.units
        ],
    }


def _tier_one(unit: TierUnit, entry: dict, plan: TierPlan, run_id: str, **kw) -> None:
    """Archive one unit and write its pointer; record the outcome in ``entry``."""
    try:
        archive_plan = plan_archive(
            unit.path, plan.destination, remote_path=unit.remote_path, allow_file=True
        )
        entry["manifest_path"] = str(archive_plan.manifest_path)
        manifest = apply_archive(
            archive_plan, remove_source=True, retire_source=True, **kw
        )
    except Exception as exc:  # noqa: BLE001 -- recorded per unit, run goes on
        entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        return
    entry.update(
        staged_path=manifest.staged_path,
        content_digest=manifest.content_digest,
        remote_path=manifest.remote_path,
    )
    try:
        pointer = _write_pointer(unit, manifest, archive_plan.manifest_path, run_id)
    except OSError as exc:
        entry.update(
            status="failed",
            error=(
                f"archived and staged, but the pointer was not written "
                f"({type(exc).__name__}: {exc}); restore by path still works"
            ),
        )
        return
    entry.update(status="tiered", pointer_path=str(pointer))


def apply_tier(
    plan: TierPlan,
    *,
    runner=None,
    retention_days: float = DEFAULT_RETENTION_DAYS,
    proc: Path = Path("/proc"),
) -> dict:
    """Move every planned unit through ``apply_archive`` and leave pointers.

    Each unit is re-checked right before it moves (still there, same kind,
    still cold, still movable as a whole, not held open, no pointer yet).
    One unit's failure never stops the others and never leaves a pointer.
    Returns the run log, which is also written to ``tier_log_dir()``.
    """
    log = _new_run_log(plan)
    log_path = Path(log["log_path"])
    try:
        atomic_json(log_path, log)
    except OSError as exc:
        raise TierRefusedError(
            f"could not write the tier run log {log_path}: {exc} -- nothing was moved."
        ) from exc

    root_device = os.lstat(plan.root).st_dev
    cutoff = time.time() - plan.cold_after_days * DAY_SECONDS
    for unit, entry in zip(plan.units, log["units"]):
        reason = _recheck(unit, root_device, cutoff, proc)
        if reason:
            entry.update(status="skipped", reason=reason)
        else:
            entry.update(status="archiving", started_at=_utc_now())
            atomic_json(log_path, log)
            _tier_one(
                unit,
                entry,
                plan,
                log["run_id"],
                retention_days=retention_days,
                runner=runner,
            )
        entry["finished_at"] = _utc_now()
        atomic_json(log_path, log)

    counts = {
        status: sum(1 for entry in log["units"] if entry["status"] == status)
        for status in ("tiered", "skipped", "failed")
    }
    log.update(
        status="completed" if not counts["failed"] else "completed-with-failures",
        counts=counts,
        tiered_bytes=sum(
            e["size_bytes"] for e in log["units"] if e["status"] == "tiered"
        ),
        finished_at=_utc_now(),
    )
    atomic_json(log_path, log)
    return log


def read_pointer(path: str | Path) -> dict | None:
    """The pointer for an ORIGINAL path, or None when it was never tiered."""
    pointer = pointer_path_for(Path(path).expanduser().resolve())
    if pointer.is_symlink() or not pointer.is_file():
        return None
    data = json.loads(pointer.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != POINTER_SCHEMA:
        raise ValueError(f"not a scitex-storage tier pointer: {pointer}")
    return data


def restore_tiered(
    path: str | Path, *, delete_remote: bool = False, runner=None
) -> Path:
    """Restore a tiered unit to its original path and remove its pointer.

    Refuses when something already occupies the original path, and when the
    pointer and the manifest disagree. The restored data must reproduce the
    manifest's content digest before the pointer is removed or, when asked,
    the remote copy is deleted.
    """
    original = Path(path).expanduser().resolve()
    pointer = read_pointer(original)
    if pointer is None:
        raise FileNotFoundError(f"no tier pointer for {original}")
    if original.exists() or original.is_symlink():
        raise FileExistsError(
            f"refusing to restore over {original}: something already occupies it"
        )
    plan = plan_restore(original)
    if plan.manifest.content_digest != pointer.get("content_digest"):
        raise ValueError(
            f"the pointer and the archive manifest for {original} disagree on the "
            "content digest; nothing was restored"
        )
    apply_restore(plan, delete_remote=delete_remote, verify_content=True, runner=runner)
    pointer_path_for(original).unlink()
    return original


# EOF
