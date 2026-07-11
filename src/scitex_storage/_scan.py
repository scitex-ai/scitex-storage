#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only storage inventory: per-top-level-child size + inode (file-count) usage.

The MVP verb behind ``scitex-storage scan``. Given one or more root
directories, it reports — for each immediate (top-level) child — the total
bytes and the total number of file inodes beneath it, sorted so the biggest
space / inode consumers surface first.

Design constraints (this tool is dogfooded on a 100%-full disk and on
inode-starved HPC trees, so it must never make either worse):

* **Read-only** — only ``os.lstat`` / ``os.scandir`` are called; file
  *contents* are never read. No hashing, no ``du``-style byte reads.
* **No symlink traversal** — symlinked directories are never followed, so
  the walk can never wander onto a slow network mount (NFS / SMB) or loop.
* **Bounded** — an optional ``max_depth`` caps recursion for login-node
  safety; the traversal is otherwise a plain local stat walk.
* **Fail-loud** — a missing / non-directory root raises, rather than being
  silently reported as empty.

Both bytes AND inodes are reported because the two crises are different:
a disk can be full of a few huge files, or starved of inodes by millions
of tiny ones. The ``FILES`` column is what surfaces an inode hog.
"""

from __future__ import annotations

import os
import stat as stat_mod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ChildUsage:
    """Space + inode usage for one top-level child of a scanned root."""

    name: str
    path: Path
    size: int  # total bytes of files beneath (os.lstat().st_size, summed)
    file_count: int  # total file inodes beneath (regular files + symlinks)
    is_dir: bool
    error: str | None = None  # set when the walk hit unreadable entries


@dataclass
class RootScan:
    """Per-top-level-child scan result for one root directory."""

    root: Path
    children: list[ChildUsage] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(c.size for c in self.children)

    @property
    def total_files(self) -> int:
        return sum(c.file_count for c in self.children)

    def by_size(self) -> list[ChildUsage]:
        """Children sorted by size (then inode count) descending."""
        return sorted(
            self.children, key=lambda c: (c.size, c.file_count), reverse=True
        )

    def by_file_count(self) -> list[ChildUsage]:
        """Children sorted by inode count (then size) descending."""
        return sorted(
            self.children, key=lambda c: (c.file_count, c.size), reverse=True
        )


def _measure_dir(
    root: Path, max_depth: int | None = None
) -> tuple[int, int, str | None]:
    """Return ``(total_size, file_count, error)`` for the tree under ``root``.

    Stat-only and never follows symlinked directories. ``max_depth``
    (relative to ``root``; ``None`` = unlimited) caps recursion depth for
    login-node safety. ``error`` is a short note when some entries could
    not be stat-ed (partial result), else ``None``.
    """
    total_size = 0
    file_count = 0
    errors = 0

    def _on_error(_exc: OSError) -> None:
        nonlocal errors
        errors += 1

    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=_on_error
    ):
        if max_depth is not None:
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
            if depth >= max_depth:
                dirnames[:] = []
        # Never descend into symlinked directories (network-hop / loop guard).
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        ]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                st = os.lstat(fpath)
            except OSError:
                errors += 1
                continue
            mode = st.st_mode
            # Count regular files and symlinks as inodes; never follow either.
            if stat_mod.S_ISREG(mode) or stat_mod.S_ISLNK(mode):
                file_count += 1
                total_size += st.st_size

    err = f"{errors} unreadable entr{'y' if errors == 1 else 'ies'}" if errors else None
    return total_size, file_count, err


def scan(root: str | Path, max_depth: int | None = None) -> RootScan:
    """Inventory ``root``'s immediate children by size and inode count.

    Read-only. Raises ``FileNotFoundError`` if ``root`` is missing and
    ``NotADirectoryError`` if it exists but is not a directory (fail-loud —
    a bad path is never silently treated as empty).
    """
    root = Path(root).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    root = root.resolve()

    result = RootScan(root=root)
    with os.scandir(root) as it:
        entries = sorted(it, key=lambda e: e.name)

    for entry in entries:
        epath = Path(entry.path)
        try:
            is_symlink = entry.is_symlink()
        except OSError:
            is_symlink = False
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            is_dir = False

        if is_dir and not is_symlink:
            size, count, err = _measure_dir(epath, max_depth=max_depth)
            result.children.append(
                ChildUsage(
                    name=entry.name,
                    path=epath,
                    size=size,
                    file_count=count,
                    is_dir=True,
                    error=err,
                )
            )
        else:
            # A file, or a symlink (dir or file) we refuse to follow: count
            # the link/file inode itself via lstat, never traverse it.
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            result.children.append(
                ChildUsage(
                    name=entry.name,
                    path=epath,
                    size=size,
                    file_count=1,
                    is_dir=False,
                )
            )
    return result


def scan_roots(
    roots: list[str | Path], max_depth: int | None = None
) -> list[RootScan]:
    """Scan several roots, returning one :class:`RootScan` per root (in order)."""
    return [scan(r, max_depth=max_depth) for r in roots]


# EOF
