#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_space.py
"""Probe the free space of a REMOTE destination before writing to it.

`sweep` learned this lesson first (card
sweep-writes-tar-to-source-filesystem-20260722): a verb that relieves a
constrained resource must not consume that resource blindly, and the
preflight has to be TWO-SIDED. An artifact-size estimate with no
destination probe passes on a full disk -- the exact defect that card
exists for -- while a destination probe with no estimate cannot say
whether what is free is ENOUGH.

`sweep` got its preflight in PR #29. `archive` and `reclaim` did not, and
they are the verbs that actually move data OFF a full filesystem. This
module supplies the missing half for a remote destination.

WHY A REMOTE PROBE AND NOT AN ASSUMPTION: "the NAS has space" is a
capability claim, and a capability claim is a measurement. nas2 has 19 TB
free today; that is a fact about today, established by asking. A verb
that assumes it will eventually meet the day it is false, and the failure
lands mid-transfer with a partial artifact on the destination.

The comparison itself is deliberately NOT reimplemented here --
:func:`scitex_storage._sweep.check_space` already returns the three-state
``SpaceVerdict`` (``ok=None`` meaning "could not answer", distinct from
``ok=False`` meaning "answered: no room"), and having two space
comparators would guarantee they drift apart.
"""

from __future__ import annotations

#: `df -Pk` is the POSIX-portable form: -P forces one line per filesystem
#: (so a long device name cannot wrap and shift the columns) and -k fixes
#: the unit at 1 KiB blocks, removing the block-size guessing that made
#: the multi-host `df` parser in `_observe` necessary. Both matter on the
#: BusyBox NAS units, whose `df` is not GNU's.
REMOTE_DF_CMD = "df -Pk {path}"


def parse_df_available_bytes(stdout: str) -> int | None:
    """Return available bytes from ``df -Pk`` output, or None.

    Returns ``None`` -- never 0 -- when the output cannot be parsed. Zero
    is a MEASUREMENT ("the destination is full"), which should refuse the
    transfer loudly; ``None`` means the question was not answered, which
    must not be collapsed into either pole.

    A focused parser rather than a call into ``_observe._df``: that module
    models a full multi-host inventory row (mount point, source,
    structural-filesystem classification, inode columns) and detects the
    block size from the header, because it consumes whatever ``df`` the
    remote happens to run. Here the command is ours and pins ``-Pk``, so
    the only question is one number.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    # -P guarantees the filesystem's data is on ONE line, so the last
    # non-empty line is the row even if df emitted a warning first.
    fields = lines[-1].split()
    if len(fields) < 4:
        return None
    try:
        return int(fields[3]) * 1024
    except ValueError:
        return None


def remote_free_bytes(destination: str, path: str, runner=None) -> int | None:
    """Ask ``destination`` how many bytes are free at ``path``.

    Returns ``None`` when the probe could not run or could not be parsed,
    so the caller sees "could not answer" rather than a fabricated number.
    ``runner`` is the same seam ``exec_remote`` takes, so a test drives
    this without a real transport and without mocks.
    """
    from scitex_ssh import exec_remote

    result = exec_remote(destination, REMOTE_DF_CMD.format(path=path), runner=runner)
    if not result.success:
        return None
    return parse_df_available_bytes(result.stdout)

# EOF
