#!/usr/bin/env python3
"""Run every applicable probe over ONE tree and compose a Classification.

WHAT WAS MISSING. Layer 1's signals are all built and merged (S1-S8), and
``classify(path, signals)`` combines them -- but NOTHING RUNS THE PROBES.
Every caller had to know which signal functions exist, which arguments
each needs, and which apply to a bare directory at all. So the classifier
was complete and unusable in the same way the regenerability detector was
complete and unreachable before it got a CLI verb: the capability existed
and no consumer could obtain it.

THE DISTINCTION THIS MODULE MAKES EXPLICIT, which the card never did and
which only became visible when the signals were listed side by side. They
are not one kind of thing:

  PER-TREE   answerable about a directory ALONE, with nothing else known:
             coldness (S1), open handles (S2), readability (S8).
             These are what "survey this tree" means.

  PER-MOVE   meaningless without a DESTINATION: destination reality (S3),
             free space (S4). Asking them about a source tree is a
             category error -- there is no destination yet to be real or
             roomy. They belong to a move preflight, and they already live
             in the verbs that move data.

  PER-SET    meaningless for ONE item: timestamp clustering (S6) reads a
             DISTRIBUTION, and a distribution of one is not evidence.
             41 agents sharing a timestamp was one fleet event; a single
             tree sharing a timestamp with itself is nothing.

  SEPARATE AXIS  duplicates (S7) and regenerability answer "does deleting
             this lose anything", not "can this move". Kept out of the
             movability verdict deliberately: a duplicate is not a holder,
             and treating one as an obstacle would block the safest
             reclaim there is.

Folding all eight into one call would have produced could-not-look for
every tree on earth, because the per-move signals can never be answered
without a destination -- a classifier that always abstains, which is the
"safe but useless" failure this package already has a card about.

THE POSITIVE CONTROL IS NOT OPTIONAL. ``open_handle_signal`` requires a
path the caller holds open, because an empty /proc scan and a blind one
are indistinguishable and the blind one returns "nothing is using this" --
the answer the caller was hoping for. This module opens its own control
file rather than letting a caller forget to.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ._classify import (
    COULD_NOT_LOOK,
    Classification,
    Signal,
    classify,
    coldness_signal,
    readability_signal,
)
from ._open_handles import open_handle_signal

#: A tree untouched this long is COLD. 14 days rather than a round 7:
#: a fortnight survives a holiday and a conference, both of which produce
#: a fortnight of silence from a corpus that is very much still wanted.
DEFAULT_COLD_AFTER_SECONDS = 14 * 24 * 3600


@dataclass(frozen=True)
class TreeStats:
    """Timestamps for a tree. ``None`` means COULD NOT READ, never zero.

    Zero is a real epoch timestamp and would read as "touched in 1970",
    which a coldness test happily calls cold. The whole point of S1 is
    that a probe which fails must not produce the convenient answer.
    """

    newest_mtime: float | None
    newest_atime: float | None
    file_count: int
    unreadable_dirs: int


def stat_tree(path: str) -> TreeStats:
    """Walk ``path`` recording the newest mtime AND atime.

    BOTH, because A READER LEAVES NO MTIME: a corpus read daily is
    byte-identical to an abandoned one under an mtime-only probe. That
    mistake nearly cost a 187 GiB tree that had been read 11 hours before
    it was proposed for deletion.

    Unreadable subdirectories are COUNTED rather than swallowed. A walk
    that silently skips what it cannot read reports a confident answer
    about a fraction of the tree -- and `os.walk` swallows errors by
    default, which is precisely how a partial measurement passes for a
    whole one.
    """
    newest_mtime: float | None = None
    newest_atime: float | None = None
    file_count = 0
    unreadable = 0

    def _onerror(_exc: OSError) -> None:
        nonlocal unreadable
        unreadable += 1

    for dirpath, dirnames, filenames in os.walk(
        path, topdown=True, onerror=_onerror, followlinks=False
    ):
        # Never descend a symlinked directory -- same doctrine as scan()
        # and sweep, so "the tree" means the same thing package-wide.
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        ]
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                st = os.lstat(fpath)
            except OSError:
                unreadable += 1
                continue
            file_count += 1
            newest_mtime = st.st_mtime if newest_mtime is None else max(
                newest_mtime, st.st_mtime
            )
            newest_atime = st.st_atime if newest_atime is None else max(
                newest_atime, st.st_atime
            )

    return TreeStats(
        newest_mtime=newest_mtime,
        newest_atime=newest_atime,
        file_count=file_count,
        unreadable_dirs=unreadable,
    )


def coverage_signal(stats: TreeStats) -> Signal:
    """Refuse a verdict drawn on a tree we could not fully read.

    This is S5's doctrine applied to ONE tree rather than to a filesystem:
    while part of the map is missing, a verdict about the whole is a guess
    wearing a number. It REFUSES rather than warning, because the failure
    being guarded against is a confident reasoner walking past a warning --
    which is exactly what happened for five hours while 681 GB sat behind
    a permission stub.

    TWO WAYS TO HAVE NO MAP, and the second one used to pass. An
    unreadable subtree is the obvious one and was always caught. The
    quiet one is a walk that succeeds and reads ZERO FILES: no error is
    raised, nothing is suppressed, and the old code reported "read every
    entry it encountered (0 files)" as MOVABLE. A count of zero over an
    empty denominator is not a clean result -- it is no result wearing a
    clean result's clothes. AN UNMOUNTED MOUNT POINT IS EXACTLY THIS: a
    readable, error-free, empty directory. For a package that manages
    three NAS units, "the NAS is not attached right now" is not an exotic
    case, and rendering it as MOVABLE points the classifier at the one
    answer that loses data.

    Named because it keeps recurring in different costumes: `pgrep` absent
    on BusyBox returning empty (reads as a count of zero), an agent
    registry returning `[]` from inside a container because it is
    host-side, a collision predicate over empty name-sets returning "no
    collisions", and a peer's near-miss reporting `missing: 0` computed
    over zero files hashed -- one step from an irreversible 7.8 GB delete,
    where the real denominator showed 46 of 1158 blobs missing.
    """
    if stats.unreadable_dirs:
        return Signal(
            name="coverage",
            verdict=COULD_NOT_LOOK,
            evidence=(
                f"{stats.unreadable_dirs} entr(y/ies) under this tree could not "
                f"be read, so the walk covered an unknown fraction of it. This "
                f"is NOT a claim that something is wrong -- it is a claim that "
                f"the map is incomplete, and a movability verdict drawn on an "
                f"incomplete map is a guess. Re-run with access to the whole "
                f"tree, or state explicitly which subtree the verdict covers."
            ),
        )
    if stats.file_count == 0:
        return Signal(
            name="coverage",
            verdict=COULD_NOT_LOOK,
            evidence=(
                "the walk completed without error but read ZERO files, and "
                "this probe cannot tell an EMPTY tree from an INVISIBLE one. "
                "An unmounted mount point is a readable, error-free, empty "
                "directory -- so is a tree whose contents live on a NAS that "
                "is not currently attached. Both produce this exact result, "
                "and only one of them is safe to act on. Confirm the "
                "filesystem is mounted and the path is the one you meant; if "
                "the tree is genuinely empty, it is trivially movable and "
                "needs no verdict from this probe."
            ),
        )
    return Signal(
        name="coverage",
        verdict="movable",
        evidence=(
            f"the walk read every entry it encountered ({stats.file_count} "
            f"files); no permission or I/O errors were suppressed"
        ),
    )


def survey(
    path: str,
    now: float | None = None,
    cold_after_seconds: float = DEFAULT_COLD_AFTER_SECONDS,
    control_path: str | None = None,
) -> Classification:
    """Run the PER-TREE probes over ``path`` and return the combined verdict.

    Deliberately does NOT run the per-move or per-set signals -- see the
    module docstring. Including them would make every verdict
    could-not-look, since neither can be answered about a lone directory.

    ``control_path`` is the positive control for the open-handle scan. If
    the caller does not supply one, this module opens its own: a scan that
    cannot see a file we KNOW is open is blind, and a blind scan returns
    "nothing is holding this" -- the reassuring answer.
    """
    now = time.time() if now is None else now

    if not os.path.isdir(path):
        return classify(
            path,
            [
                Signal(
                    name="exists",
                    verdict=COULD_NOT_LOOK,
                    evidence=(
                        f"{path} is not a directory (absent, or a regular "
                        f"file). Nothing was measured -- this is not a "
                        f"verdict about the tree, it is the absence of one."
                    ),
                )
            ],
        )

    stats = stat_tree(path)
    signals = [
        readability_signal(path),
        coverage_signal(stats),
        coldness_signal(
            newest_mtime=stats.newest_mtime,
            newest_atime=stats.newest_atime,
            now=now,
            cold_after_seconds=cold_after_seconds,
        ),
    ]

    if control_path is not None:
        signals.append(open_handle_signal(path, control_path))
    else:
        # Open our own control so the scan is never trusted unverified.
        # __file__ is guaranteed to exist and be readable by this process.
        with open(__file__, "rb") as control:
            signals.append(open_handle_signal(path, control.name))
            _ = control.read(1)  # keep the handle genuinely open for the scan

    return classify(path, signals)

# EOF
