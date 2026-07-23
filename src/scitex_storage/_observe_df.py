#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_observe_df.py
"""Parsing and classification of ``df`` output -- no I/O.

Split out of ``_observe`` (which re-exports these). Portability is the
whole point: the fleet spans GNU/Linux, macOS and BusyBox appliances, so
everything here works from POSIX ``df -P`` / ``df -Pi`` and assumes
nothing GNU-specific. Every rule that looks over-careful was paid for by
a real wrong answer against the live fleet.
"""

from __future__ import annotations

from typing import Sequence

#: What we ask a remote host. POSIX-portable on GNU, BSD/macOS and BusyBox.
DF_SPACE_CMD = "df -P"
DF_INODE_CMD = "df -Pi"

#: Device-name prefixes/names whose "100% full" is structural, not a
#: warning. A read-only squashfs image (every `/snap/*`, every
#: appliance `/rootfs/*`) is packed exactly full BY CONSTRUCTION -- it
#: can never be anything else. A first live run across this fleet
#: produced dozens of 100% rows from these alone, which is how a
#: dashboard trains its reader to ignore red.
#:
#: Note this is decided from the SOURCE DEVICE, not the mount path.
#: Paths are a naming convention and vary per OS; `/dev/loop*` is what
#: the kernel actually reports.
#:
#: The list is deliberately per-form rather than per-concept, because
#: the SAME concept reports differently per host: the appliance NASes
#: mount their squashfs images as ``/dev/loop*`` while Ubuntu/WSL mounts
#: the identical thing as ``snapfuse``. A first version of this rule was
#: written from the NAS output alone, caught 233 rows, and still left 25
#: WSL snaps flagged -- a filter built from the contaminants you have
#: already seen only excludes the contaminants you have already seen.
PSEUDO_SOURCES = (
    "tmpfs",
    "devtmpfs",
    "udev",
    "devfs",
    "map",
    "overlay",
    "none",
    "snapfuse",
    "squashfs",
)
IMAGE_SOURCE_PREFIXES = ("/dev/loop",)


def is_structural(source: str) -> bool:
    """True when this filesystem's fullness carries no information.

    Squashfs images are always 100% full; tmpfs/devfs are memory, not
    storage. Reporting either as a capacity alarm is a false positive,
    and a dashboard full of false positives is worse than none -- it
    teaches the reader that red means nothing.
    """
    if source in PSEUDO_SOURCES:
        return True
    return source.startswith(IMAGE_SOURCE_PREFIXES)


def parse_df_posix(text: str) -> list[dict[str, object]]:
    """Parse ``df -P``/``df -Pi`` output into rows.

    POSIX ``df -P`` guarantees one line per filesystem after the header,
    with the mount point LAST. It does NOT guarantee a column count:
    GNU ``df -Pi`` emits six fields, macOS emits nine
    (``... Capacity iused ifree %iused Mounted on``). Assuming six and
    slicing ``parts[5:]`` therefore swallows macOS's inode columns into
    the mount name, no mount ever matches, and every Mac filesystem
    reports "inodes unavailable" -- which is a wrong answer that looks
    like a limitation. Found by running this against the real fleet;
    the unit tests, written from GNU output, were perfectly happy.

    So the mount column index is read from the HEADER (``Mounted on``)
    rather than assumed, and the first three numeric columns after the
    filesystem name are taken as total/used/available -- which holds for
    both the block and inode variants on every df in this fleet.

    Rows that still do not parse are DROPPED rather than guessed at, and
    the caller sees a shorter list rather than a plausible fiction.
    """
    lines = text.splitlines()
    if not lines:
        return []

    header = lines[0].split()
    try:
        # "Mounted on" is two words; the mount VALUE starts at that index.
        mount_idx = header.index("Mounted")
    except ValueError:
        mount_idx = 5  # no recognisable header: fall back to the POSIX shape

    block_bytes = _block_bytes(header)
    rows: list[dict[str, object]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) <= mount_idx:
            continue
        try:
            total = int(parts[1])
            used = int(parts[2])
            avail = int(parts[3])
        except (ValueError, IndexError):
            # A "-" or "none" column: common for tmpfs/devfs and for
            # inode columns on filesystems without a fixed inode table.
            continue
        rows.append(
            {
                "source": parts[0],
                "total": total,
                "used": used,
                "avail": avail,
                # The block UNIT differs by OS: GNU `df -P` uses 1024-byte
                # blocks, macOS uses 512 (POSIX). Assuming 1024 everywhere
                # doubled every macOS size -- so the unit is read from the
                # header, and byte totals are computed from it, never from
                # a hardcoded multiplier.
                "block_bytes": block_bytes,
                "mount": " ".join(parts[mount_idx:]),
            }
        )
    return rows


def _block_bytes(header: list[str]) -> int:
    """Bytes per block, read from the df header column name.

    ``512-blocks`` -> 512 (macOS/POSIX); ``1024-blocks`` / ``1K-blocks``
    -> 1024 (GNU). Inode output (``Inodes``) has no block column and the
    value is irrelevant there; default 1024. A header we do not recognise
    also defaults to 1024, matching GNU, the most common case.
    """
    for tok in header:
        if tok.endswith("-blocks"):
            num = tok[: -len("-blocks")]
            if num.isdigit():
                return int(num)
            if num.upper() == "1K":
                return 1024
    return 1024


def used_pct(total: int, used: int) -> float | None:
    """Percentage used, or ``None`` when the total is zero/absent.

    Returns ``None`` rather than ``0.0`` for an empty filesystem table.
    Zero is a measurement meaning "nothing used"; ``None`` means "there
    was nothing to measure", and rendering the second as the first is how
    a dead mount becomes a reassuring green bar.
    """
    if total <= 0:
        return None
    return round(100.0 * used / total, 1)


def index_by_mount(
    rows: Sequence[dict[str, object]]
) -> dict[str, dict[str, object]]:
    """Index parsed df rows by their mount point."""
    return {str(r["mount"]): r for r in rows}

# EOF
