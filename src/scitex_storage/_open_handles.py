#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_open_handles.py
"""S2 -- is anything holding this tree open RIGHT NOW?

The one question no other signal answers. Coldness (S1) tells you nothing
was read or written recently; regenerability tells you a tree could be
rebuilt. Neither says whether a live process has a file open at this
instant, and that is the difference between a safe move and yanking the
floor out from under a running job.

Paid for twice on 2026-07-22: five reclaim candidates on ywata-note-win
were each withdrawn under measurement, and four of five "dead agent"
overlays turned out to belong to LIVE agents. "Regenerable" was true of
all of them and would have licensed deleting every one.

THE POSITIVE CONTROL IS NOT OPTIONAL, and it is the whole reason this
module has a strange-looking signature.

A scan of ``/proc`` that finds nothing is indistinguishable from a scan
that could not run: both produce an empty set, and the empty set reads as
"nothing is using this" -- the convenient answer, which is exactly what
the caller hoped for. Under a container, a restricted ``/proc``, or a
hardened kernel, the scan silently returns nothing at all.

So :func:`open_handle_signal` REQUIRES a control path that the caller
knows is held open, and refuses to answer unless the probe actually finds
it. A probe that cannot see a file we are holding open ourselves has not
demonstrated anything about the file we care about.

WHAT THE CONTROL DOES AND DOES NOT LICENSE, recorded because it is easy
to over-read: a control chosen because you expect it to pass validates
the MECHANISM and is SILENT ABOUT COVERAGE. It proves this process can
see ITS OWN open handles through ``/proc``. It cannot vouch for a class
of holder the scan does not enumerate -- another user's processes
(whose ``/proc/<pid>/fd`` is unreadable), another mount or PID namespace,
a remote NFS client, or a kernel-internal reference. So a MOVABLE verdict
here means "no holder that this scan can see", never "no holder".
"""

from __future__ import annotations

import os
from typing import Iterable, Iterator

from ._classify import COULD_NOT_LOOK, MOVABLE, NOT_MOVABLE, Signal

PROC_ROOT = "/proc"


def iter_open_paths(proc_root: str = PROC_ROOT) -> Iterator[str]:
    """Yield every filesystem path this process can observe as open.

    Reads ``/proc/<pid>/fd/*`` symlinks and the file-backed entries of
    ``/proc/<pid>/maps`` (so a memory-mapped file -- a running binary, a
    loaded shared library, an mmap'd dataset -- counts as a holder, which
    an fd-only scan would miss entirely).

    Unreadable pids are SKIPPED SILENTLY here on purpose: another user's
    processes are simply invisible, and that is a coverage limit reported
    by the caller's evidence string, not an error. Raising would make the
    common case (a multi-user host) fail rather than answer.
    """
    try:
        pids = [name for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return

    for pid in pids:
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            for fd in os.listdir(fd_dir):
                try:
                    yield os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
        except OSError:
            pass

        maps_path = os.path.join(proc_root, pid, "maps")
        try:
            with open(maps_path, "r", errors="replace") as handle:
                for line in handle:
                    # "addr perms offset dev inode  /path/to/file"
                    parts = line.rstrip("\n").split(None, 5)
                    if len(parts) == 6 and parts[5].startswith("/"):
                        yield parts[5]
        except OSError:
            pass


def holders_under(tree: str, open_paths: Iterable[str]) -> list[str]:
    """Return the observed open paths that live under ``tree``.

    Compares on a normalised prefix with a trailing separator, so
    ``/data/foo`` does NOT match ``/data/foobar`` -- a plain
    ``startswith`` would report a holder for an unrelated sibling and
    block a perfectly movable tree.
    """
    root = os.path.realpath(tree).rstrip(os.sep)
    prefix = root + os.sep
    return sorted({p for p in open_paths if p == root or p.startswith(prefix)})


def open_handle_signal(
    tree: str,
    control_path: str,
    open_paths: Iterable[str] | None = None,
) -> Signal:
    """Is anything holding ``tree`` open, as far as this process can see?

    ``control_path`` MUST be a path the caller currently holds open. If
    the scan cannot find it, the probe is broken or blind and the verdict
    is ``COULD_NOT_LOOK`` -- never ``MOVABLE``. This is the guard that
    stops an empty result set from being read as "nothing is using this".

    ``open_paths`` exists so a test can drive the pure comparison with a
    known set; passing ``None`` runs the real ``/proc`` scan.
    """
    observed = list(iter_open_paths() if open_paths is None else open_paths)

    control = os.path.realpath(control_path)
    if not any(p == control for p in observed):
        return Signal(
            "open-handles",
            COULD_NOT_LOOK,
            (
                f"POSITIVE CONTROL FAILED: the scan did not find "
                f"{control!r}, which the caller is holding open. An empty "
                f"result therefore proves nothing -- a blind probe and an "
                f"unused tree are indistinguishable, and the blind one "
                f"produces the answer you were hoping for. Refusing to "
                f"report this tree as unused."
            ),
        )

    holders = holders_under(tree, observed)
    if holders:
        shown = ", ".join(holders[:3])
        more = f" (+{len(holders) - 3} more)" if len(holders) > 3 else ""
        return Signal(
            "open-handles",
            NOT_MOVABLE,
            f"{len(holders)} open handle(s) under {tree}: {shown}{more}",
        )

    return Signal(
        "open-handles",
        MOVABLE,
        (
            f"no open handle under {tree} among {len(observed)} observed "
            f"paths, and the positive control was found -- so the scan ran. "
            f"COVERAGE CAVEAT: this sees only holders readable from this "
            f"process; another user's processes, another PID or mount "
            f"namespace, and remote clients are NOT enumerated."
        ),
    )

# EOF
