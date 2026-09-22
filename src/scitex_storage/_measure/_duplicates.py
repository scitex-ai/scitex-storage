#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact duplicate-file detection — a separate, explicitly opt-in verb.

``scan`` (see ``_scan.py``) is deliberately **stat-only**: it never reads
file *contents*, precisely so it is always safe to point at a nearly-full
disk or a slow network mount (a byte-reading "du-storm" is exactly the
failure mode ``scan`` was reworked to avoid — see the PR that introduced
the per-child size+inode design). Finding *exact* duplicates fundamentally
requires reading (hashing) file contents, so that capability cannot live
inside ``scan`` without breaking its own safety contract. It is instead its
own verb, ``find-duplicates`` / :func:`find_duplicates`, that an operator
must explicitly choose to run.

PERFORMANCE: even so, a hand-rolled Python ``hashlib`` size+hash pass is
the wrong tool at multi-terabyte scale — the same rationale as ``scan``'s
``fd`` delegation. This module shells out to ``fclones``
(https://github.com/pkolaczk/fclones), an established, actively-maintained
Rust duplicate-file finder that already implements a highly efficient
group-by-size, then parallel-hash-prefix, then parallel-hash-suffix, then
full-content-hash pipeline (minimizing bytes actually read compared to a
naive "hash every candidate fully" approach) instead of a hand-rolled
reimplementation. ``fclones`` is a **system** (non-PyPI) runtime dependency
of this verb only (see ``_system_deps.py`` and the README) — never required
to *install* scitex-storage. A missing binary raises
:class:`~scitex_storage._measure._scan.MissingSystemDependencyError` with install
instructions rather than silently falling back to a slow pure-Python hash.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ._scan import MissingSystemDependencyError

_FCLONES_BINARY_NAME = "fclones"

_FCLONES_INSTALL_HINT = """scitex-storage `find-duplicates` requires the `fclones` binary — a \
hand-rolled Python size+hash pass is too slow at multi-terabyte scale.

`fclones` was not found on PATH. Install it:
  cargo:          cargo install fclones
  brew:           brew install fclones
  other/manual:   https://github.com/pkolaczk/fclones/releases

See https://github.com/pkolaczk/fclones for details."""


def _fclones_binary() -> str:
    """Return the path to ``fclones``.

    Raises :class:`MissingSystemDependencyError` (never falls back to a
    Python hash pass) if it is not on ``PATH``.
    """
    found = shutil.which(_FCLONES_BINARY_NAME)
    if found:
        return found
    raise MissingSystemDependencyError(_FCLONES_INSTALL_HINT)


def find_duplicates(
    roots: list[str | Path], max_depth: int | None = None
) -> list[list[Path]]:
    """Find groups of files with byte-identical content under ``roots``.

    Unlike :func:`scitex_storage.scan`, this READS FILE CONTENTS (via
    ``fclones``'s parallel prefix/suffix/full-content hashing) — there is
    no stat-only equivalent by definition; exact duplicate detection
    requires reading bytes. Use ``max_depth`` to bound the walk on a slow
    network mount or a login node.

    Read-only in the sense that nothing is moved, linked, or deleted —
    ``fclones group`` (not ``fclones link``/``remove``/``dedupe``) only
    ever reports.

    Raises ``FileNotFoundError`` / ``NotADirectoryError`` for a bad root
    (fail-loud, matching :func:`scitex_storage.scan`) and
    :class:`MissingSystemDependencyError` if ``fclones`` is not installed.
    """
    if not roots:
        return []

    resolved = _resolve_dir_roots(roots)

    fclones_bin = _fclones_binary()
    cmd = [fclones_bin, "group", "--hidden", "--no-ignore", "--format", "json"]
    if max_depth is not None:
        cmd += ["--depth", str(max_depth)]
    cmd += [str(p) for p in resolved]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"`fclones group` exited {proc.returncode}: {stderr or '(no stderr output)'}"
        )

    payload = json.loads(proc.stdout.decode("utf-8"))
    groups: list[list[Path]] = []
    for group in payload.get("groups", []):
        paths = sorted(Path(p) for p in group.get("files", []))
        if len(paths) >= 2:
            groups.append(paths)
    groups.sort(key=lambda g: len(g), reverse=True)
    return groups


def reclaimable_bytes(groups: list[list[Path]]) -> int | None:
    """Bytes recoverable by keeping ONE copy from each duplicate group.

    Sums ``(len(group) - 1) * size`` -- deleting every copy would be data
    loss, not reclamation, so the first is never counted. Sizes come from
    ``lstat`` on the first readable member: fclones has already proven the
    group byte-identical, so any member's size is the group's size.

    Returns ``None`` -- never 0 -- when no group could be measured at all.
    Zero is a MEASUREMENT ("duplicates exist but recover nothing", which is
    real for empty files) and must stay distinct from "the question was not
    answered".
    """
    total = 0
    measured_any = False
    for group in groups:
        size = None
        for member in group:
            try:
                size = member.lstat().st_size
                break
            except OSError:
                continue
        if size is None:
            continue
        measured_any = True
        total += (len(group) - 1) * size
    return total if measured_any else None


def duplicates_signal(groups: list[list[Path]] | None) -> "Signal":
    """S7 -- the ZERO-RISK reclaim class, as a classifier signal.

    Duplicates are the only class that frees space with nothing moved,
    nothing lost and no owner consulted: one copy of a byte-identical set
    is redundant by definition. Card
    movability-classifier-deterministic-signals-20260723 says this should
    run FIRST in any reclaim sequence for exactly that reason.

    THE VERDICT DIRECTION IS THE SUBTLE PART, and it is the opposite of
    what "duplicates found" suggests. Finding duplicates does NOT make a
    tree movable, and finding none does not block it -- duplication is a
    property of file CONTENT, while movability is about whether anything
    is standing on the tree. So this reports MOVABLE when a measurement
    was obtained (with the recoverable total as evidence, which is what a
    human actually acts on) and COULD_NOT_LOOK when the scan could not
    run. It never returns NOT_MOVABLE: a duplicate is not a holder, and
    treating one as an obstacle would block the safest reclaim there is.

    ``groups=None`` means the scan did not run -- fclones missing, a
    permission denial, a bounded walk that never completed. That is
    COULD_NOT_LOOK, not "no duplicates": an unrun scan and a clean tree
    both produce an empty answer, and only one of them is evidence.
    """
    from ._classify import COULD_NOT_LOOK, MOVABLE, Signal

    if groups is None:
        return Signal(
            "duplicates",
            COULD_NOT_LOOK,
            "duplicate scan did not run -- an unrun scan and a tree with no "
            "duplicates both look like 'no groups', and only one of them is "
            "evidence",
        )
    if not groups:
        return Signal(
            "duplicates",
            MOVABLE,
            "duplicate scan ran and found no byte-identical groups -- no "
            "zero-risk reclaim available here",
        )

    recoverable = reclaimable_bytes(groups)
    if recoverable is None:
        return Signal(
            "duplicates",
            COULD_NOT_LOOK,
            f"{len(groups)} duplicate group(s) found but NONE could be "
            f"sized (every member unreadable) -- the reclaim cannot be "
            f"quantified, so it must not be reported as a number",
        )
    redundant = sum(len(g) - 1 for g in groups)
    return Signal(
        "duplicates",
        MOVABLE,
        f"{len(groups)} duplicate group(s), {redundant} redundant copies, "
        f"{recoverable} bytes recoverable by keeping one of each -- the "
        f"zero-risk class: nothing moves, nothing is lost, no owner needs "
        f"consulting. Run this before any move.",
    )


def _resolve_dir_roots(roots: list[str | Path]) -> list[Path]:
    """Validate ``roots`` exactly as :func:`find_duplicates` does.

    Shared so the stat-only stage and the hashing stage agree on what a
    root is; fail-loud on a bad root in both stages.
    """
    resolved: list[Path] = []
    for raw_root in roots:
        p = Path(raw_root).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"path does not exist: {p}")
        if not p.is_dir():
            raise NotADirectoryError(f"not a directory: {p}")
        resolved.append(p.resolve())
    return resolved


def size_groups(
    roots: list[str | Path], max_depth: int | None = None
) -> dict[int, list[Path]]:
    """Stat-only stage: group files by byte size, WITHOUT reading contents.

    The cheap pre-pass before hashing: same size is necessary (not
    sufficient) for byte-identity, so sizes shared by 2+ files are exactly
    the candidate set ``fclones`` would hash. On a slow network mount this
    answers "how much COULD overlap" in a stat-only walk, letting the
    operator decide whether the content-reading stage is worth it.

    Symlinked directories are never descended (same rule as
    :func:`scitex_storage.scan` -- no network-mount storms); symlinked
    files are skipped. Only sizes with 2+ files are returned, biggest
    size first. Empty dict means no candidates, not an error.
    """
    by_size: dict[int, list[Path]] = {}

    def walk(directory: str, depth: int) -> None:
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if max_depth is None or depth < max_depth:
                        walk(entry.path, depth + 1)
                elif entry.is_file(follow_symlinks=False):
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    by_size.setdefault(size, []).append(Path(entry.path))
            except OSError:
                continue

    for root in _resolve_dir_roots(roots):
        walk(str(root), 0)
    return {
        size: sorted(paths)
        for size, paths in sorted(by_size.items(), reverse=True)
        if len(paths) >= 2
    }


def candidate_bytes(by_size: dict[int, list[Path]]) -> int | None:
    """Bytes at stake in a :func:`size_groups` result.

    ``(n - 1) * size`` per shared size -- the most the hashing stage
    could confirm as redundant. ``None`` (never 0) when there are no
    candidates at all; 0 is a real measurement (only empty files share
    sizes), matching the :func:`reclaimable_bytes` convention.
    """
    if not by_size:
        return None
    return sum((len(paths) - 1) * size for size, paths in by_size.items())


def overlap_bytes(
    groups: list[list[Path]], roots: list[str | Path]
) -> dict[str, dict[str, int]]:
    """Cross-root overlap matrix from confirmed duplicate ``groups``.

    ``matrix[A][B]`` (A != B) is the bytes under root A that also exist
    under root B -- the "how much of dump A is inside dump B" answer for
    same-content-different-layout trees, where a name-based diff reports
    zero overlap. ``matrix[A][A]`` is the redundant bytes strictly inside
    A. Every pair is present (0 when nothing overlaps); sizes come from
    ``lstat`` on a readable member, like :func:`reclaimable_bytes`.
    """
    resolved = [Path(r).expanduser().resolve() for r in roots]

    def owner(path: Path) -> str | None:
        for root in resolved:
            if path == root or root in path.parents:
                return str(root)
        return None

    matrix: dict[str, dict[str, int]] = {
        str(a): {str(b): 0 for b in resolved} for a in resolved
    }
    for group in groups:
        members: dict[str, list[Path]] = {}
        for member in group:
            root = owner(member)
            if root is not None:
                members.setdefault(root, []).append(member)
        if not members:
            continue
        size: int | None = None
        for member in group:
            try:
                size = member.lstat().st_size
                break
            except OSError:
                continue
        if size is None:
            continue
        for root_a, paths_a in members.items():
            for root_b in members:
                if root_b == root_a:
                    continue
                matrix[root_a][root_b] += len(paths_a) * size
            if len(paths_a) >= 2:
                matrix[root_a][root_a] += (len(paths_a) - 1) * size
    return matrix


# EOF
