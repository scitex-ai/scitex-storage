#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_accounting.py
"""S5 -- does what we measured add up to what the filesystem reports?

IF MEASURED << REPORTED, SUSPECT SCOPE BEFORE PRECISION.

This is the single check that would have ended the 2026-07-22
ywata-note-win incident in its first hour, and it is worth stating
exactly how it failed instead of being caught:

* ``df`` said 1.9 T used. Everything anyone could measure summed to about
  786 G. That 1.1 T gap was visible IMMEDIATELY.
* It was explained away as measurement error THREE TIMES while the
  instruments were upgraded -- ``du -sh``, then ``du --max-depth``, then
  ``gdu`` -- each time inside a boundary that never contained the answer.
* The answer was 681 GB in ``/var/lib/docker``: root-only, so ``du``
  returned a 4.0K permission STUB rather than an error, and a separate
  mount, so ``du -x`` skipped it entirely. It was invisible to every
  home-directory-scoped instrument, and no amount of precision inside
  that scope would ever have found it.

The lesson is not "measure better". It is that a large unexplained
residual is EVIDENCE ABOUT THE BOUNDARY, not noise in the measurement --
and the correct response is to stop reasoning inside that boundary until
the residual is accounted for.

SO THIS REFUSES RATHER THAN WARNS. The failure mode being prevented is
precisely "continued to reason confidently inside a scope that did not
contain the answer", and a warning is something a confident reasoner
walks straight past. While the residual is unexplained, every movability
verdict for a tree on that filesystem is COULD_NOT_LOOK -- not because
the tree is suspicious, but because the map is known to be incomplete
and a verdict drawn on an incomplete map is a guess wearing a number.

A residual can be perfectly innocent (sparse files, compression,
reserved blocks, a mount the scan legitimately excluded). The signal does
not claim otherwise. It claims the residual has not been EXPLAINED, and
requires someone to explain it -- which is a different and much weaker
claim than "something is wrong", and the right one.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._classify import COULD_NOT_LOOK, MOVABLE, Signal

#: A residual under this fraction of reported usage is treated as within
#: the noise a sound measurement produces (sparse files, block rounding,
#: reserved space). Above it, the scope itself is in question.
#:
#: 10% is deliberately generous: the incident's residual was 58% of
#: reported usage. A threshold tight enough to fire on ordinary rounding
#: would be muted within a week, and a muted gate is a deleted one.
RESIDUAL_THRESHOLD = 0.10


@dataclass(frozen=True)
class Accounting:
    """What was measured, what the filesystem reports, and the gap.

    ``residual_fraction`` is of REPORTED usage rather than of the
    measured total: the question is "how much of this filesystem is
    unaccounted for", and dividing by the measured total would flatter
    exactly the case where the measurement missed the most.
    """

    measured_bytes: int
    reported_used_bytes: int

    @property
    def residual_bytes(self) -> int:
        return self.reported_used_bytes - self.measured_bytes

    @property
    def residual_fraction(self) -> float:
        if self.reported_used_bytes <= 0:
            return 0.0
        return self.residual_bytes / self.reported_used_bytes


def accounting_signal(
    measured_bytes: int | None,
    reported_used_bytes: int | None,
    threshold: float = RESIDUAL_THRESHOLD,
    explained_bytes: int = 0,
    explanation: str = "",
) -> Signal:
    """Refuse to vouch for a filesystem whose usage does not add up.

    ``explained_bytes`` lets a caller account for a residual it
    UNDERSTANDS -- a mount deliberately excluded from the scan, say --
    and like every other allowance in this package it requires a written
    ``explanation``. An unexplained allowance is a fudge factor, and a
    fudge factor here would restore exactly the "explained away as
    measurement error" behaviour this signal exists to stop.

    A NEGATIVE residual (measured more than df reports) is also
    unexplained, and is not silently treated as fine: it usually means
    hardlinks or bind mounts counted twice, which means the measurement
    is not what the caller thinks it is.
    """
    if explained_bytes and not explanation.strip():
        raise ValueError(
            f"refusing an unexplained allowance of {explained_bytes} bytes -- "
            f"an allowance with no stated reason is how a residual gets "
            f"'explained away as measurement error', which is the exact "
            f"failure this signal exists to prevent"
        )
    if measured_bytes is None or reported_used_bytes is None:
        return Signal(
            "accounting",
            COULD_NOT_LOOK,
            (
                "accounting could not be computed (measured="
                f"{measured_bytes}, reported={reported_used_bytes}) -- a "
                "filesystem whose usage was never totalled cannot vouch for "
                "anything on it"
            ),
        )

    acct = Accounting(
        measured_bytes=measured_bytes + explained_bytes,
        reported_used_bytes=reported_used_bytes,
    )
    fraction = acct.residual_fraction

    if abs(fraction) <= threshold:
        return Signal(
            "accounting",
            MOVABLE,
            (
                f"accounting reconciles: measured {acct.measured_bytes} vs "
                f"reported {reported_used_bytes} "
                f"({fraction:.1%} residual, within {threshold:.0%})"
                + (f"; {explained_bytes} explained as {explanation}" if explained_bytes else "")
            ),
        )

    if fraction < 0:
        return Signal(
            "accounting",
            COULD_NOT_LOOK,
            (
                f"measured MORE than the filesystem reports: {acct.measured_bytes} "
                f"vs {reported_used_bytes} ({-acct.residual_bytes} bytes over). "
                f"Usually hardlinks or a bind mount counted twice -- which means "
                f"the measurement is not what it appears to be. Resolve before "
                f"trusting any verdict derived from it."
            ),
        )

    return Signal(
        "accounting",
        COULD_NOT_LOOK,
        (
            f"UNEXPLAINED RESIDUAL: {acct.residual_bytes} bytes "
            f"({fraction:.1%} of reported usage) are unaccounted for -- "
            f"measured {acct.measured_bytes}, filesystem reports "
            f"{reported_used_bytes}. SUSPECT SCOPE BEFORE PRECISION: a gap "
            f"this size is evidence the measurement BOUNDARY is wrong, not "
            f"that the instrument is imprecise. Look for a root-only "
            f"directory returning a permission stub instead of an error, or "
            f"a separate mount a one-filesystem walk skipped. Refusing to "
            f"vouch for anything on this filesystem until it is explained."
        ),
    )

# EOF
