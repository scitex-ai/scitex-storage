#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_classify.py
"""Layer 1 (MECHANICAL) of storage management: deterministic movability signals.

Three layers, per the operator's 2026-07-23 request:

* **MECHANICAL (this module)** -- no judgment. Same input, same answer.
  Measures whether a tree *can physically move* and whether anything is
  standing on it. It never decides whether something is *worth keeping*.
* **GUI / INTENT** -- the human supplies value: still wanted? which tier?
  keep-forever or disposable? approve this move?
* **AGENTIC** -- context and reasoning: who owns this, what produces the
  growth, which of three copies is canonical. Proposes; never executes.

The invariant that makes the split safe::

    agent proposes -> GUI shows evidence -> human approves -> mechanical
    executes and verifies

Every rule here was paid for by the 2026-07-22 ywata-note-win incident,
where five carefully-analysed reclaim candidates were each withdrawn
under measurement while the real 681 GB sat in a directory nobody could
read. The rules that survived:

**NEVER ONE SIGNAL -- require two that can disagree.** Single-signal
verdicts failed in *both* directions that night: "written recently, so
the owner is alive" (a container *build* wrote a dead agent's tree) and
"no writes in 15 days, so it is unused" (something had *read* it 11
hours earlier). Both substituted an adjacent predicate for the one
actually asked.

**A READER LEAVES NO MTIME.** Coldness needs atime paired with mtime, or
a corpus in daily use is indistinguishable from an abandoned one.

**A failed probe and a true negative are indistinguishable at the call
site.** So absence is never reported as "fine" -- it is
``COULD_NOT_LOOK``, a distinct state, and the caller must handle it.
The failure mode always produces the *convenient* answer ("nothing is
using this"), which is exactly what the caller hoped to hear.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Iterable, Sequence

#: A tree may move: every applicable signal agreed, and said so from a
#: measurement that actually ran.
MOVABLE = "movable"
#: Something is standing on it, or it cannot physically go where asked.
NOT_MOVABLE = "not-movable"
#: A probe could not run, a permission was denied, or two signals that
#: answer the same question disagreed. NOT a synonym for "no" -- it means
#: "this question was not answered", and it must never be rendered as a
#: reassuring green zero.
COULD_NOT_LOOK = "could-not-look"

_VERDICTS = (MOVABLE, NOT_MOVABLE, COULD_NOT_LOOK)


@dataclass(frozen=True)
class Signal:
    """One mechanical measurement and the evidence that produced it.

    ``evidence`` is mandatory and is meant to be shown to a human, because
    a verdict without its evidence cannot be audited -- and every wrong
    call during the incident looked exactly as confident as the right
    ones.
    """

    name: str
    verdict: str
    evidence: str

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise ValueError(
                f"{self.name}: verdict {self.verdict!r} is not one of {_VERDICTS}"
            )
        if not self.evidence.strip():
            raise ValueError(
                f"{self.name}: refusing a verdict with no evidence -- a "
                f"verdict that cannot be audited is not a measurement"
            )


@dataclass(frozen=True)
class Classification:
    """The combined verdict for one path, with every signal retained."""

    path: str
    verdict: str
    signals: tuple[Signal, ...]

    @property
    def reason(self) -> str:
        """The signals that decided the verdict, for display."""
        deciding = [s for s in self.signals if s.verdict == self.verdict]
        return "; ".join(f"{s.name}: {s.evidence}" for s in deciding)


def combine(signals: Sequence[Signal]) -> str:
    """Combine signals into one verdict, conservatively.

    Order matters and is deliberate:

    1. **No signals at all -> COULD_NOT_LOOK.** An empty measurement is
       not a clean bill of health.
    2. **Any COULD_NOT_LOOK -> COULD_NOT_LOOK.** If one probe did not
       run, the others cannot cover for it -- they answer different
       questions, so they cannot vouch for the one that is missing.
    3. **Any NOT_MOVABLE -> NOT_MOVABLE.** One holder is enough. This is
       not a vote; a single "something is standing on this" outranks any
       number of agreements, because the cost is asymmetric.
    4. Otherwise MOVABLE.

    There is deliberately no majority rule. During the incident the
    disagreements were where the truth was, and any tie-break would have
    buried them.
    """
    if not signals:
        return COULD_NOT_LOOK
    verdicts = [s.verdict for s in signals]
    if COULD_NOT_LOOK in verdicts:
        return COULD_NOT_LOOK
    if NOT_MOVABLE in verdicts:
        return NOT_MOVABLE
    return MOVABLE


def classify(path: str, signals: Sequence[Signal]) -> Classification:
    """Bundle signals for ``path`` into an auditable Classification."""
    return Classification(
        path=path, verdict=combine(signals), signals=tuple(signals)
    )


# --------------------------------------------------------------------------
# S1 -- coldness: mtime PAIRED with atime
# --------------------------------------------------------------------------
def coldness_signal(
    newest_mtime: float | None,
    newest_atime: float | None,
    now: float,
    cold_after_seconds: float,
) -> Signal:
    """Cold only if BOTH the newest write AND the newest read are old.

    A reader leaves no mtime, so an mtime-only probe reports an actively
    read corpus as abandoned. This is the exact call that nearly sent
    187 GiB of a benchmark corpus to archive while something was reading
    it 11 hours earlier.

    Pass ``None`` for a timestamp that could not be determined -- that is
    a COULD_NOT_LOOK, never an implicit "old".

    Caveat callers must respect: under ``relatime`` an atime bump means
    "read within the last day", NOT "read once". A modest atime signal is
    not evidence of light usage.
    """
    if newest_mtime is None or newest_atime is None:
        missing = "mtime" if newest_mtime is None else "atime"
        return Signal(
            name="coldness",
            verdict=COULD_NOT_LOOK,
            evidence=f"newest {missing} could not be determined",
        )
    write_age = now - newest_mtime
    read_age = now - newest_atime
    if read_age < cold_after_seconds:
        return Signal(
            name="coldness",
            verdict=NOT_MOVABLE,
            evidence=(
                f"READ {read_age / 86400:.1f}d ago (threshold "
                f"{cold_after_seconds / 86400:.1f}d) -- a reader leaves no mtime"
            ),
        )
    if write_age < cold_after_seconds:
        return Signal(
            name="coldness",
            verdict=NOT_MOVABLE,
            evidence=f"written {write_age / 86400:.1f}d ago",
        )
    return Signal(
        name="coldness",
        verdict=MOVABLE,
        evidence=(
            f"no write for {write_age / 86400:.1f}d and no read for "
            f"{read_age / 86400:.1f}d"
        ),
    )


# --------------------------------------------------------------------------
# S3 -- destination reality: an unmounted mountpoint is the perfect trap
# --------------------------------------------------------------------------
def destination_signal(
    dest_fsid: int | None, source_fsid: int | None, dest_in_mounts: bool
) -> Signal:
    """Refuse a destination that is merely a DIRECTORY NAMED like a mount.

    ``/mnt/d``, ``/mnt/nas`` and ``/mnt/nas2`` were all bare directories
    on the full root filesystem. Writing "to the NAS" would have poured
    data onto the dying disk -- no error, no warning, every signal
    reporting success -- and then "it is safely on the nas" licenses
    deleting the only good copy.

    An unmounted mountpoint is the perfect could-not-look: it does not
    fail, it accepts the data. The only way to tell a mount from a
    directory named like one is to ask the kernel.
    """
    if dest_fsid is None or source_fsid is None:
        return Signal(
            name="destination",
            verdict=COULD_NOT_LOOK,
            evidence="could not stat the filesystem of source or destination",
        )
    if not dest_in_mounts:
        return Signal(
            name="destination",
            verdict=NOT_MOVABLE,
            evidence=(
                "destination is NOT in /proc/mounts -- it is a directory, "
                "not a mount; writes would land on the source filesystem"
            ),
        )
    if dest_fsid == source_fsid:
        return Signal(
            name="destination",
            verdict=NOT_MOVABLE,
            evidence=(
                f"destination shares the source filesystem (fsid {dest_fsid}) "
                f"-- moving there frees nothing"
            ),
        )
    return Signal(
        name="destination",
        verdict=MOVABLE,
        evidence=f"distinct mounted filesystem (fsid {dest_fsid} vs {source_fsid})",
    )


# --------------------------------------------------------------------------
# S4 -- free-space preflight, TWO-SIDED
# --------------------------------------------------------------------------
def free_space_signal(
    needed_bytes: int | None, available_bytes: int | None, margin: float = 1.05
) -> Signal:
    """Compare an estimate against a REAL probe of the destination.

    One-sided is theatre. An estimate with no destination probe passes on
    a full disk -- which is how ``sweep`` came to write its tar beside the
    source and would have driven a 2.3 GB-free filesystem to zero while
    "cleaning up". A destination probe with no estimate cannot say whether
    what is available is *enough*.

    ``margin`` keeps a little headroom; filling a filesystem to exactly
    zero is its own outage.
    """
    if needed_bytes is None or available_bytes is None:
        which = "artifact size" if needed_bytes is None else "destination free space"
        return Signal(
            name="free-space",
            verdict=COULD_NOT_LOOK,
            evidence=f"{which} could not be determined",
        )
    required = int(needed_bytes * margin)
    if available_bytes < required:
        short = required - available_bytes
        return Signal(
            name="free-space",
            verdict=NOT_MOVABLE,
            evidence=(
                f"destination short by {short / 1e9:.1f} GB "
                f"(need {required / 1e9:.1f} GB, have {available_bytes / 1e9:.1f} GB)"
            ),
        )
    return Signal(
        name="free-space",
        verdict=MOVABLE,
        evidence=(
            f"destination has {available_bytes / 1e9:.1f} GB, "
            f"need {required / 1e9:.1f} GB"
        ),
    )


# --------------------------------------------------------------------------
# S6 -- timestamp clustering: N items, one event
# --------------------------------------------------------------------------
def clustering_signal(
    timestamps: Iterable[float], tolerance_seconds: float = 60.0
) -> Signal:
    """Refuse per-item verdicts when one event explains every timestamp.

    Forty-one agents did not independently stop inside a six-hour window,
    and thirty-seven overlays were not independently written 0.3 days ago
    -- that was a single hook push writing the same file into every one.
    Reading the DISTRIBUTION rather than the total is a separate check,
    and it caught two false conclusions in one night.
    """
    values = sorted(timestamps)
    if len(values) < 3:
        return Signal(
            name="clustering",
            verdict=MOVABLE,
            evidence=f"only {len(values)} timestamp(s) -- clustering not applicable",
        )
    spread = values[-1] - values[0]
    if spread <= tolerance_seconds:
        return Signal(
            name="clustering",
            verdict=COULD_NOT_LOOK,
            evidence=(
                f"all {len(values)} timestamps fall within {spread:.0f}s -- "
                f"one event, not {len(values)} independent facts"
            ),
        )
    return Signal(
        name="clustering",
        verdict=MOVABLE,
        evidence=f"{len(values)} timestamps spread over {spread / 86400:.1f}d",
    )


# --------------------------------------------------------------------------
# S8 -- permission stub: unreadable is not empty
# --------------------------------------------------------------------------
def readability_signal(path: str) -> Signal:
    """Distinguish "measured empty" from "could not read".

    ``du`` on a root-owned ``0710`` directory returns the stub size and
    exits 0. ``/var/lib/docker`` reported "4.0K" that way while holding
    681 GB -- the entire answer to a night-long investigation, rendered
    as a number that looked like a measurement.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        return Signal(
            name="readability",
            verdict=COULD_NOT_LOOK,
            evidence=f"cannot stat {path}: {exc.strerror}",
        )
    if not stat.S_ISDIR(st.st_mode):
        return Signal(
            name="readability",
            verdict=MOVABLE,
            evidence=f"{path} is a file, readable",
        )
    if not os.access(path, os.R_OK | os.X_OK):
        return Signal(
            name="readability",
            verdict=COULD_NOT_LOOK,
            evidence=(
                f"{path} is not readable -- any size reported for it is a "
                f"permission stub, not a measurement"
            ),
        )
    return Signal(
        name="readability",
        verdict=MOVABLE,
        evidence=f"{path} is readable",
    )

# EOF
