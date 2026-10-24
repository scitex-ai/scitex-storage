#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_retention.py
"""Restorable staging for verified moves (operator 2026-10-24).

A move is only safe if the original survives verification AND stays
recoverable for a while after: rsync + checksum validate the copy, then
the source moves to a restorable staging area instead of being deleted
outright. A sweeper deletes staged trees once their retention expires
(default 7 days).

Staging lives NEXT TO the source (``<parent>/.stx-restorable/``), so the
move is an atomic same-filesystem rename that needs no extra space. A
central registry tracks every staged tree so the sweeper can find them.

Deletion guards (each recorded, never silent):
* only paths inside a ``.stx-restorable`` directory are ever removed --
  a tampered or hand-edited registry entry pointing elsewhere is refused;
* the staged tree must still carry its marker file with a matching
  original path -- something a user repurposed the directory for is left
  alone;
* symlinks are unlinked, never followed.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

#: Staging directory name, always a direct child of the source's parent.
STAGING_DIRNAME = ".stx-restorable"

#: Marker file written inside every staged tree; the sweeper only removes
#: trees whose marker matches the registry entry.
MARKER_FILENAME = ".stx-restorable-record.json"

#: Operator 2026-10-24: a moved original stays restorable for a week.
DEFAULT_RETENTION_DAYS = 7.0


@dataclass(frozen=True)
class StagedRecord:
    """One staged (moved-aside, not yet deleted) source tree."""

    staged_path: str
    original_path: str
    staged_at: float
    delete_after: float


@dataclass
class SweepReport:
    """What one sweeper pass saw and did."""

    removed: list[str]
    retained: list[str]
    pruned: list[str]
    refused: list[str]
    errors: dict[str, str]


def _registry_path() -> Path:
    """Central registry of staged trees (resolved fresh, sandbox-friendly)."""
    return Path("~/.scitex/scitex-storage/runtime/restorable-registry.json").expanduser()


def _read_registry() -> list[dict]:
    try:
        data = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_registry(entries: list[dict]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def stage_for_retention(
    source: str | Path, *, retention_days: float = DEFAULT_RETENTION_DAYS
) -> StagedRecord:
    """Move ``source`` aside into restorable staging; return the record.

    Same-filesystem atomic rename (no extra space, no copy window).
    Raises ``FileNotFoundError`` for a missing source. A collision on the
    staged name appends ``-N`` rather than overwriting.
    """
    src = Path(source)
    if not src.exists() and not src.is_symlink():
        raise FileNotFoundError(f"nothing to stage: {source}")
    staging = src.parent / STAGING_DIRNAME
    staging.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(
        time.time(), tz=datetime.timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}--{src.name}"
    target = staging / base
    counter = 1
    while target.exists() or target.is_symlink():
        counter += 1
        target = staging / f"{base}-{counter}"
    os.replace(src, target)
    staged_at = time.time()
    record = StagedRecord(
        staged_path=str(target),
        original_path=str(src),
        staged_at=staged_at,
        delete_after=staged_at + retention_days * 86400,
    )
    marker = {"original_path": str(src), "staged_at": staged_at,
              "delete_after": record.delete_after}
    if target.is_dir() and not target.is_symlink():
        marker_path = target / MARKER_FILENAME
    else:
        # A staged file cannot hold its own marker: a sibling carries it.
        marker_path = target.parent / (target.name + "." + MARKER_FILENAME)
    marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    entries = _read_registry()
    entries.append(asdict(record))
    _write_registry(entries)
    return record


def _marker_path_for(staged: Path) -> Path:
    if staged.is_dir() and not staged.is_symlink():
        return staged / MARKER_FILENAME
    return staged.parent / (staged.name + "." + MARKER_FILENAME)


def _marker_matches(staged: Path, entry: dict) -> bool:
    """True when the staged tree still carries its own staging marker."""
    try:
        marker = json.loads(_marker_path_for(staged).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(marker, dict)
        and marker.get("original_path") == entry.get("original_path")
    )


def sweep_restorable(
    *, dry_run: bool = False, now: float | None = None
) -> SweepReport:
    """Delete staged trees whose retention expired; report everything.

    ``dry_run`` reports without touching the disk. Registry entries whose
    tree is already gone are pruned; entries pointing outside a
    ``.stx-restorable`` directory, or whose marker no longer matches, are
    refused (recorded, never deleted).
    """
    moment = time.time() if now is None else now
    report = SweepReport(removed=[], retained=[], pruned=[], refused=[], errors={})
    entries = _read_registry()
    kept: list[dict] = []
    for entry in entries:
        staged = Path(entry.get("staged_path", ""))
        if staged.parent.name != STAGING_DIRNAME or not staged.exists():
            if not staged.exists() and staged.parent.name == STAGING_DIRNAME:
                report.pruned.append(str(staged))
                continue
            report.refused.append(str(staged))
            kept.append(entry)
            continue
        if moment < float(entry.get("delete_after", 0)):
            report.retained.append(str(staged))
            kept.append(entry)
            continue
        if not _marker_matches(staged, entry):
            report.refused.append(str(staged))
            kept.append(entry)
            continue
        if dry_run:
            report.removed.append(str(staged))
            kept.append(entry)
            continue
        try:
            if staged.is_symlink() or staged.is_file():
                staged.unlink()
                # The sibling marker does not live inside the removed file.
                _marker_path_for(staged).unlink(missing_ok=True)
            else:
                shutil.rmtree(staged)
        except OSError as exc:
            report.errors[str(staged)] = str(exc)
            kept.append(entry)
            continue
        report.removed.append(str(staged))
    _write_registry(kept)
    return report
