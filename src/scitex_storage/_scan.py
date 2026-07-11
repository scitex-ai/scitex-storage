#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only directory-tree scan: discovery + size x staleness scoring.

Implements exactly the "first MVP" slice from the project's design notes
(``scitex-storage scan ~/projects``): walk a tree, skip regenerable
directories, score each file by ``size_bytes * days_since_last_access``,
and (optionally) group same-size/same-hash files as duplicate candidates.

This module never writes, moves, or deletes anything — it only reads
``os.stat`` and (for the duplicate pass) file bytes for hashing.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# Regenerable / vendored directories the design notes call out as
# "全部無視" (ignore entirely) for space-reclaim purposes — build artifacts,
# vendored deps, caches. Matched by directory *name*, anywhere in the tree.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".eggs",
    }
)


def _is_excluded(name: str, exclude_dirs: frozenset[str]) -> bool:
    return name in exclude_dirs or name.endswith(".egg-info")


@dataclass
class FileEntry:
    """One scanned file: its path (relative to the scan root), size, and
    last-access time."""

    path: Path
    size: int
    atime: float  # epoch seconds, os.stat().st_atime

    def days_since_access(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, (now - self.atime) / 86400.0)

    def score(self, now: float | None = None) -> float:
        """``score = size_bytes * days_since_last_access`` (design-doc heuristic)."""
        return self.size * self.days_since_access(now)


@dataclass
class ScanResult:
    root: Path
    files_scanned: int = 0
    dirs_scanned: int = 0
    total_size: int = 0
    skipped_size: int = 0
    skipped_dirs: set[str] = field(default_factory=set)
    entries: list[FileEntry] = field(default_factory=list)
    duplicate_groups: list[list[Path]] = field(default_factory=list)
    scan_time: float = field(default_factory=time.time)

    def top_candidates(self, top: int = 20) -> list[FileEntry]:
        return sorted(
            self.entries, key=lambda e: e.score(self.scan_time), reverse=True
        )[:top]


def walk_tree(
    root: Path,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> tuple[list[FileEntry], int, int]:
    """Walk ``root``, returning ``(entries, dirs_scanned, skipped_size)``.

    Directories whose *name* matches ``exclude_dirs`` are pruned entirely
    (never descended into); their on-disk size is best-effort summed into
    ``skipped_size`` for the report, but their contents are not scanned.
    """
    entries: list[FileEntry] = []
    dirs_scanned = 0
    skipped_size = 0

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        keep: list[str] = []
        for d in dirnames:
            if _is_excluded(d, exclude_dirs):
                skipped_size += _dir_size_best_effort(Path(dirpath) / d)
            else:
                keep.append(d)
        dirnames[:] = keep
        dirs_scanned += 1

        for fname in filenames:
            fpath = Path(dirpath) / fname
            try:
                st = fpath.lstat()
            except OSError:
                continue
            if not os.path.isfile(fpath) or os.path.islink(fpath):
                # Skip symlinks (and anything stat can't confirm is a
                # regular file) — this MVP only reports plain files.
                continue
            entries.append(FileEntry(path=fpath, size=st.st_size, atime=st.st_atime))

    return entries, dirs_scanned, skipped_size


def _dir_size_best_effort(path: Path) -> int:
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for fname in filenames:
                try:
                    total += (Path(dirpath) / fname).lstat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(entries: list[FileEntry]) -> list[list[Path]]:
    """Group files that share (size, sha256) — a cheap, exact duplicate
    detector in the spirit of ``fdupes``/``rdfind``.

    Files are first bucketed by size (free — already known from the walk);
    only files sharing a size bucket with >= 2 members are hashed, so a
    tree with no same-size collisions costs zero hashing.
    """
    by_size: dict[int, list[FileEntry]] = {}
    for e in entries:
        if e.size == 0:
            continue  # empty files are not useful "duplicates" to report
        by_size.setdefault(e.size, []).append(e)

    groups: list[list[Path]] = []
    for size, candidates in by_size.items():
        if len(candidates) < 2:
            continue
        by_hash: dict[str, list[Path]] = {}
        for e in candidates:
            try:
                digest = _hash_file(e.path)
            except OSError:
                continue
            by_hash.setdefault(digest, []).append(e.path)
        for paths in by_hash.values():
            if len(paths) >= 2:
                groups.append(sorted(paths))

    groups.sort(key=lambda g: len(g), reverse=True)
    return groups


def scan(
    root: str | Path,
    top: int = 20,
    dedupe: bool = True,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> ScanResult:
    """Scan ``root`` and return a :class:`ScanResult`.

    Read-only: only stats and (for ``dedupe``) reads file bytes. Never
    writes, moves, or deletes anything.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    entries, dirs_scanned, skipped_size = walk_tree(root, exclude_dirs)
    total_size = sum(e.size for e in entries)

    result = ScanResult(
        root=root,
        files_scanned=len(entries),
        dirs_scanned=dirs_scanned,
        total_size=total_size,
        skipped_size=skipped_size,
        skipped_dirs=set(exclude_dirs),
        entries=entries,
    )
    if dedupe:
        result.duplicate_groups = find_duplicates(entries)
    return result


# EOF
