"""Filesystem identity and conservative savings for content-duplicate groups."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _file_key(path: Path, info: os.stat_result):
    # Some filesystems expose no inode number. Do not collapse every such file.
    return (info.st_dev, info.st_ino) if info.st_ino else ("path", path.resolve())


def _regular_copies(paths, *, ignore_missing=False):
    """Keep regular-file inodes once; only ignore genuinely vanished paths."""
    seen = set()
    copies = []
    observed = False
    for path in sorted(set(map(Path, paths))):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if ignore_missing:
                continue
            raise
        observed = True
        if not stat.S_ISREG(info.st_mode):
            continue
        key = _file_key(path, info)
        if key not in seen:
            seen.add(key)
            copies.append((path, info))
    return copies, observed


def reclaimable_bytes(groups: list[list[Path]]) -> int | None:
    """Conservative logical bytes from independently removable duplicate files.

    Symlinks contribute no target bytes, and multiple names for one inode are
    one copy. Retain hard-linked inodes: removing one reported name does not
    free their data. Other single-link copies can be removed while retaining
    one physical copy. Missing members are not assumed to occupy space.
    This does not measure filesystem compression, sparse allocation or reflinks.
    ``None`` means identity/size evidence is unavailable or inconsistent,
    including a non-missing filesystem error. Measured zero stays zero.
    """
    total = 0
    measured = False
    seen = set()
    for group in groups:
        try:
            copies, observed = _regular_copies(group, ignore_missing=True)
        except OSError:
            return None  # Permission/I/O/path errors are not vanished files.
        measured = measured or observed
        fresh = []
        for path, info in copies:
            key = _file_key(path, info)
            if key not in seen:
                seen.add(key)
                fresh.append(info)
        if len(fresh) < 2:
            continue
        sizes = {info.st_size for info in fresh}
        if len(sizes) != 1:
            return None  # A file changed after the content comparison.
        size = sizes.pop()
        single_links = sum(info.st_nlink == 1 for info in fresh)
        retained_hardlink = any(info.st_nlink > 1 for info in fresh)
        removable = single_links if retained_hardlink else single_links - 1
        total += max(0, removable) * size
    return total if measured else None
