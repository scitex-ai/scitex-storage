#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only storage inventory: per-top-level-child size + inode (file-count) usage.

The MVP verb behind ``scitex-storage scan``. Given one or more root
directories, it reports — for each immediate (top-level) child — the total
bytes and the total number of file inodes beneath it, sorted so the biggest
space / inode consumers surface first.

Design constraints (this tool is dogfooded on a 100%-full disk and on
inode-starved HPC trees, so it must never make either worse):

* **Read-only** — file *contents* are never read. No hashing, no
  ``du``-style byte reads.
* **No symlink traversal** — symlinked directories are never followed, so
  the walk can never wander onto a slow network mount (NFS / SMB) or loop.
* **Bounded** — an optional ``max_depth`` caps recursion for login-node
  safety; the traversal is otherwise a plain stat-only walk.
* **Fail-loud** — a missing / non-directory root raises, rather than being
  silently reported as empty.

Both bytes AND inodes are reported because the two crises are different:
a disk can be full of a few huge files, or starved of inodes by millions
of tiny ones. The ``FILES`` column is what surfaces an inode hog.

PERFORMANCE: this tool exists to be pointed at multi-terabyte,
multi-million-file trees (a 4TB laptop NVMe, a 2TB WSL volume, 4x4TB NAS
SSDs, 4x10TB NAS HDDs). A pure-Python ``os.walk`` is the wrong tool at that
scale, so the per-child walk shells out to ``fd`` (fd-find,
https://github.com/sharkdp/fd) -- an established, actively-maintained Rust
CLI -- instead of a hand-rolled reimplementation. ``fd`` is a **system**
(non-PyPI) runtime dependency of ``scan`` only (see ``_system_deps.py`` and
the README); it is never required to *install* scitex-storage. A missing
binary raises :class:`MissingSystemDependencyError` with install
instructions rather than silently falling back to a slow pure-Python walk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_FD_BINARY_NAMES: tuple[str, ...] = ("fd", "fdfind")

_FD_INSTALL_HINT = """scitex-storage `scan` requires the `fd` binary (fd-find) for fast, \
parallel directory traversal — a plain Python `os.walk` is too slow at \
multi-terabyte / multi-million-file scale.

Neither `fd` nor `fdfind` was found on PATH. Install one:
  Debian/Ubuntu:  sudo apt install fd-find   (installs the binary as `fdfind`)
  macOS:          brew install fd
  cargo:          cargo install fd-find
  other/manual:   https://github.com/sharkdp/fd#installation

See https://github.com/sharkdp/fd for details."""


class MissingSystemDependencyError(RuntimeError):
    """A required system (non-PyPI) CLI binary is not on ``PATH``.

    Raised at *call* time (never at import time) by :func:`scan` /
    :func:`scan_roots` (needs ``fd``). Deliberately **not** caught anywhere
    in this package — a silent fallback to a slow pure-Python walk at
    multi-TB scale would be far worse than a loud, actionable error.
    """


def _fd_binary() -> str:
    """Return the path to ``fd`` (or the Debian/Ubuntu ``fdfind`` alias).

    Raises :class:`MissingSystemDependencyError` (never falls back to a
    Python walk) if neither is on ``PATH``.
    """
    for name in _FD_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise MissingSystemDependencyError(_FD_INSTALL_HINT)


@dataclass
class ChildUsage:
    """Space + inode usage for one top-level child of a scanned root."""

    name: str
    path: Path
    size: int  # total bytes of files beneath (os.lstat().st_size, summed)
    file_count: int  # total file inodes beneath (regular files + symlinks)
    is_dir: bool
    newest_mtime: float = 0.0  # max st_mtime among the child itself + everything beneath
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


def _fd_search(
    fd_bin: str, root: Path, file_type: str, max_depth: int | None
) -> tuple[list[str], int]:
    """Run one ``fd --type <file_type>`` search under ``root``.

    Returns ``(paths, errors)``. ``errors`` counts fd's own stderr warning
    lines (fd exits 0 even when it hits an unreadable subdirectory, one
    warning line per problem) -- a non-zero exit is a different, real
    failure (bad args, root vanished mid-scan, ...) and raises instead.
    """
    cmd = [
        fd_bin,
        "--hidden",
        "--no-ignore",
        "--absolute-path",
        "--print0",
        "--type",
        file_type,
    ]
    if max_depth is not None:
        # fd's --max-depth is 1-indexed from the search root (--max-depth 1
        # lists only files directly in root); this function's max_depth is
        # 0-indexed (max_depth=0 means "root's own files only" too, by the
        # historical os.walk-based contract test_scan_max_depth_bounds_
        # recursion pins) -- so the fd-facing value is max_depth + 1.
        cmd += ["--max-depth", str(max_depth + 1)]
    cmd += ["--", ".", str(root)]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"`{' '.join(cmd)}` exited {proc.returncode}: {stderr or '(no stderr output)'}"
        )
    errors = sum(1 for line in proc.stderr.splitlines() if line.strip())
    paths = [os.fsdecode(raw) for raw in proc.stdout.split(b"\0") if raw]
    return paths, errors


def _measure_dir(
    root: Path, max_depth: int | None = None
) -> tuple[int, int, float | None, str | None]:
    """Return ``(total_size, file_count, newest_file_mtime, error)`` for ``root``.

    Stat-only and never follows symlinked directories. ``max_depth``
    (relative to ``root``; ``None`` = unlimited) caps recursion depth for
    login-node safety. ``newest_file_mtime`` is the max ``st_mtime`` among
    files found during the walk, or ``None`` if none were found — computed
    in the same pass as size/count rather than a second directory
    traversal, which matters on filesystems where high inode utilization
    makes metadata ops slow. Deliberately NOT seeded from any directory's
    own mtime: a directory's mtime updates whenever an entry is added or
    removed inside it, which is unrelated to (and can be far newer or older
    than) its files' own content-modification times — mixing the two would
    corrupt the "how recently was this actually touched" signal callers
    rely on (e.g. `sweep`'s freshness exclusion). ``error`` is a short note
    when some entries could not be read/stat-ed (partial result), else
    ``None``.

    The walk itself is delegated to ``fd`` (see module docstring); the only
    Python-side work per result is a cheap ``lstat()``/``isdir()`` check —
    never the bottleneck at scale, only the walk was.

    Two separate ``fd`` searches, matching the historical
    ``os.walk``-based semantics exactly:

    * ``--type file`` — plain regular files. ``fd`` never classifies a
      symlink as ``file`` (even one pointing at a regular file), so this
      set never needs a symlink check.
    * ``--type symlink`` — every symlink ``fd`` finds (it does not need to
      *follow* a symlink to discover it, only to descend past it, and
      ``fd`` never does the latter without ``-L``). Only the ones whose
      *target* is NOT itself a directory count as an inode here: a symlink
      to a directory is exactly the case ``os.walk``'s ``dirnames``-pruning
      excluded entirely (never traversed, never counted) — a symlink to a
      file, or a dangling symlink, is counted (matching "count regular
      files and symlinks as inodes; never follow either").
    """
    fd_bin = _fd_binary()
    total_size = 0
    file_count = 0
    errors = 0
    newest_file_mtime: float | None = None

    file_paths, file_errors = _fd_search(fd_bin, root, "file", max_depth)
    errors += file_errors
    for fpath in file_paths:
        try:
            st = os.lstat(fpath)
        except OSError:
            errors += 1
            continue
        file_count += 1
        total_size += st.st_size
        if newest_file_mtime is None or st.st_mtime > newest_file_mtime:
            newest_file_mtime = st.st_mtime

    link_paths, link_errors = _fd_search(fd_bin, root, "symlink", max_depth)
    errors += link_errors
    for fpath in link_paths:
        if os.path.isdir(fpath):  # follows the link to check the TARGET's type
            continue  # symlink to a directory: never traversed, never counted
        try:
            st = os.lstat(fpath)  # the symlink's own inode, never the target's
        except OSError:
            errors += 1
            continue
        file_count += 1
        total_size += st.st_size
        if newest_file_mtime is None or st.st_mtime > newest_file_mtime:
            newest_file_mtime = st.st_mtime

    err = f"{errors} unreadable entr{'y' if errors == 1 else 'ies'}" if errors else None
    return total_size, file_count, newest_file_mtime, err


def scan(root: str | Path, max_depth: int | None = None) -> RootScan:
    """Inventory ``root``'s immediate children by size and inode count.

    Read-only. Raises ``FileNotFoundError`` if ``root`` is missing and
    ``NotADirectoryError`` if it exists but is not a directory (fail-loud —
    a bad path is never silently treated as empty). Raises
    :class:`MissingSystemDependencyError` if ``fd`` is not installed.
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
            size, count, newest_file_mtime, err = _measure_dir(epath, max_depth=max_depth)
            if newest_file_mtime is not None:
                newest_mtime = newest_file_mtime
            else:
                # No files anywhere beneath -- fall back to the directory's
                # own mtime so an empty-of-files-but-recently-touched dir
                # still reports something meaningful.
                try:
                    newest_mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    newest_mtime = 0.0
            result.children.append(
                ChildUsage(
                    name=entry.name,
                    path=epath,
                    size=size,
                    file_count=count,
                    is_dir=True,
                    newest_mtime=newest_mtime,
                    error=err,
                )
            )
        else:
            # A file, or a symlink (dir or file) we refuse to follow: count
            # the link/file inode itself via lstat, never traverse it.
            try:
                st = entry.stat(follow_symlinks=False)
                size = st.st_size
                mtime = st.st_mtime
            except OSError:
                size = 0
                mtime = 0.0
            result.children.append(
                ChildUsage(
                    name=entry.name,
                    path=epath,
                    size=size,
                    file_count=1,
                    is_dir=False,
                    newest_mtime=mtime,
                )
            )
    return result


def scan_roots(
    roots: list[str | Path], max_depth: int | None = None
) -> list[RootScan]:
    """Scan several roots, returning one :class:`RootScan` per root (in order)."""
    return [scan(r, max_depth=max_depth) for r in roots]


# EOF
