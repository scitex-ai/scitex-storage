#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rotation for a directory of versioned files (SIF images and friends).

The MVP verb behind ``scitex-storage images prune``. Many SciTeX build
pipelines (sac's Apptainer SIFs first among them) write one dated file per
build into a directory and point a symlink (``<name>.sif``) at whichever one
is currently live — e.g.::

    containers/sac-base/sac-base-2026-0710-233008.sif
    containers/sac-base.sif -> sac-base/sac-base-2026-0710-233008.sif

Rotation must never delete the file a live symlink resolves to — deleting a
"just-superseded-looking" but still-referenced image is exactly the class of
incident this verb exists to prevent (a load-bearing SIF was deleted by an
age-only sweep before this tool existed).

Design constraints:

* **Referenced files are never candidates.** A file is referenced iff some
  symlink directly inside the scanned directory resolves to it. Referenced
  files are excluded from the removal set regardless of ``keep``.
* **Plan/apply split.** :func:`plan_prune` only computes what *would* be
  removed; it never touches the filesystem. :func:`apply_prune` is the only
  function that unlinks anything, and only ever unlinks paths that were in
  the plan's ``remove`` list moments earlier (fail-loud re-check).
* **Referenced-but-not-open is not enough.** A dated file can drop out of
  the symlink target (mid-swap) while a running process still has it open
  (mmap'd apptainer instance, an agent that booted before the swap). Right
  before unlinking, :func:`apply_prune` also checks ``/proc/*/fd`` for any
  process with the candidate open and skips it (loudly) rather than
  unlinking — a second, independent guard against the same incident class
  as the symlink check, learned from a real load-bearing-SIF deletion.
* **Domain-agnostic.** Nothing here is SIF-specific; ``pattern`` defaults to
  ``*.sif`` but the logic is a generic versioned-file rotation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PruneCandidate:
    """One file directly inside the scanned directory matching ``pattern``."""

    path: Path
    size: int
    mtime: float


@dataclass
class PrunePlan:
    """The result of :func:`plan_prune` — never partially applied."""

    directory: Path
    referenced: list[PruneCandidate] = field(default_factory=list)
    kept: list[PruneCandidate] = field(default_factory=list)
    remove: list[PruneCandidate] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(c.size for c in self.remove)


@dataclass
class SkippedInUse:
    """A ``plan.remove`` candidate :func:`apply_prune` refused to unlink."""

    candidate: PruneCandidate
    pids: list[int]


@dataclass
class ApplyResult:
    """The result of :func:`apply_prune`."""

    removed: list[PruneCandidate] = field(default_factory=list)
    skipped_in_use: list[SkippedInUse] = field(default_factory=list)

    @property
    def reclaimed_bytes(self) -> int:
        return sum(c.size for c in self.removed)


def _referenced_targets(directory: Path) -> set[Path]:
    """Resolve every symlink directly inside ``directory`` to its target.

    Only targets that themselves resolve to a path inside ``directory`` are
    returned — an external symlink cannot protect a candidate here, and a
    target outside the directory is out of scope for this rotation.
    """
    targets: set[Path] = set()
    with os.scandir(directory) as it:
        for entry in it:
            if not entry.is_symlink():
                continue
            try:
                resolved = Path(entry.path).resolve()
            except OSError:
                continue
            if resolved.parent == directory:
                targets.add(resolved)
    return targets


def plan_prune(
    directory: str | Path, keep: int, pattern: str = "*.sif"
) -> PrunePlan:
    """Compute (never execute) a prune plan for ``directory``.

    ``keep`` is the target total retained count; referenced files always
    survive on top of it, so the actual number kept can exceed ``keep`` when
    more than ``keep`` files are currently referenced. Candidates are the
    regular (non-symlink) files directly in ``directory`` matching
    ``pattern``; the directory's own symlinks are consulted only to compute
    the referenced set, never treated as candidates themselves.

    Fail-loud on a missing / non-directory ``directory``.
    """
    directory = Path(directory).expanduser()
    if not directory.exists():
        raise FileNotFoundError(f"path does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")
    directory = directory.resolve()

    referenced_targets = _referenced_targets(directory)

    candidates: list[PruneCandidate] = []
    with os.scandir(directory) as it:
        entries = list(it)
    for entry in entries:
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            continue
        epath = Path(entry.path)
        if not epath.match(pattern):
            continue
        st = entry.stat(follow_symlinks=False)
        candidates.append(
            PruneCandidate(path=epath, size=st.st_size, mtime=st.st_mtime)
        )

    referenced = [c for c in candidates if c.path in referenced_targets]
    unreferenced = sorted(
        (c for c in candidates if c.path not in referenced_targets),
        key=lambda c: c.mtime,
        reverse=True,
    )

    keep_budget = max(0, keep - len(referenced))
    kept_unreferenced = unreferenced[:keep_budget]
    remove = unreferenced[keep_budget:]

    return PrunePlan(
        directory=directory,
        referenced=referenced,
        kept=referenced + kept_unreferenced,
        remove=remove,
    )


def _pids_with_open_fd(path: Path) -> list[int]:
    """PIDs of processes with ``path`` currently open, via ``/proc`` scan.

    Pure ``/proc/*/fd`` introspection — no ``lsof``/``fuser`` subprocess
    dependency, matching this package's read-only, stat-only design.
    Silently skips any ``/proc/<pid>/fd`` this process cannot read (a
    process owned by a different user); on a single-user agent-container
    host that covers every process able to have a load-bearing SIF open.
    Returns an empty list (never "in use") on a non-Linux host, where
    ``/proc`` does not exist.
    """
    try:
        resolved = os.fspath(path.resolve())
    except OSError:
        return []
    try:
        proc_pids = [n for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return []

    pids: list[int] = []
    for pid in proc_pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target == resolved:
                pids.append(int(pid))
                break
    return pids


def apply_prune(plan: PrunePlan) -> ApplyResult:
    """Execute a prune plan: unlink every path in ``plan.remove``.

    Never called implicitly — the CLI requires an explicit ``--apply``, and
    :func:`plan_prune` never mutates the filesystem on its own. For each
    candidate, immediately before unlinking:

    1. Re-checks it is still a plain (non-symlink) file — raises rather
       than removing anything that changed shape since the plan was
       computed (e.g. became a symlink).
    2. Checks ``/proc`` for any process with it currently open — if any
       are found, the candidate is skipped (not unlinked, not raised) and
       recorded in ``ApplyResult.skipped_in_use`` so the caller can report
       it loudly. This is a second, independent guard on top of the
       referenced-symlink check: a file can drop out of the symlink target
       mid-swap while a process that booted from it is still running.
    """
    removed: list[PruneCandidate] = []
    skipped: list[SkippedInUse] = []
    for c in plan.remove:
        if not c.path.exists() or c.path.is_symlink() or not c.path.is_file():
            raise FileExistsError(
                f"refusing to remove {c.path}: no longer a plain file "
                "(changed since the plan was computed?)"
            )
        pids = _pids_with_open_fd(c.path)
        if pids:
            skipped.append(SkippedInUse(candidate=c, pids=pids))
            continue
        c.path.unlink()
        removed.append(c)
    return ApplyResult(removed=removed, skipped_in_use=skipped)


# EOF
