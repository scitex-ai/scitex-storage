#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_redundancy.py
"""Redundancy coverage: does this data actually survive losing something?

scitex-storage's job is storage HYGIENE for a lab -- circulation,
management, and keeping data alive -- across whatever storage exists
(ywata-note-win, nas, nas1, nas2, spartan, mba, and whatever comes
next). Capacity is the easy half. This module is the other half: for a
given dataset, is it actually protected, and against what?

The target is **3-2-1-1-0**:

===  ===================================================================
3    at least three copies
2    on at least two different media
1    at least one off-site
1    at least one offline or immutable (WORM / Object Lock)
0    ZERO restore-verification errors
===  ===================================================================

**The 0 is the part everyone skips, and it is the one that decides
whether any of the rest was real.** An unverified copy is a belief, not
a backup: nothing distinguishes a good archive from a corrupt one until
something reads it back. So this module REFUSES to count a copy that has
never been restore-verified, and downgrades one whose verification has
gone stale. That is deliberately harsher than most tooling, and it is
the same rule that governed the 2026-07-22 migration -- a copy is not a
migration until the destination has been read back.

**Not all data deserves the same protection.** Protecting everything
equally is how backup budgets die, and it buries the small critical set
under bulk. The criticality classes below carry very different
policies, and ``REGENERABLE`` deliberately requires *nothing*: a docker
image, a cache, a thumbnail or a search index should be rebuilt, not
preserved. Separating "the original, which is gone forever if lost" from
"the artifact, which can be remade" is the single highest-leverage
distinction in the whole system.

A note on what a *copy* is not: a RAID mirror is not a copy, and neither
is a snapshot on the same appliance. Both survive a disk dying; neither
survives deletion, ransomware, fire, or theft of the box. Callers should
model those as ONE copy, because that is what they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

@dataclass(frozen=True)
class Policy:
    """What one class of data demands. Supplied by the CALLER, not by us.

    scitex-storage is a mechanism for handling storage. WHICH classes
    exist, what they are called, and how hard each is protected are the
    operator's decisions and belong in their config -- a lab archiving
    microscopy, a clinic under retention law and a studio holding video
    masters have nothing in common except the shape of the question.

    So there is no built-in class list and no default policy table here.
    A class name is valid precisely when the caller supplied a policy
    for it. ``rpo_seconds``/``rto_seconds`` are carried for reporting;
    this module does not schedule anything.
    """

    min_copies: int
    min_media: int
    require_offsite: bool
    require_immutable: bool
    #: How stale a restore verification may be before the copy stops
    #: counting. ``None`` means this class does not require verification
    #: -- appropriate for regenerable data, and for nothing else.
    max_verify_age_seconds: float | None
    rpo_seconds: float = float("inf")
    rto_seconds: float = float("inf")


@dataclass(frozen=True)
class Copy:
    """One copy of a dataset, somewhere, on something.

    ``restore_verified_at`` is when this copy was last READ BACK and
    checked -- not when it was last written to. Writing proves the
    source could be read; only a restore proves the copy can be.
    """

    host: str
    path: str
    medium: str
    offsite: bool = False
    immutable: bool = False
    restore_verified_at: float | None = None

    def counts(self, policy: Policy, now: float) -> bool:
        """Whether this copy may be counted toward coverage.

        A copy that has never been restore-verified does not count, and
        neither does one whose verification has gone stale. This is the
        ``0`` in 3-2-1-1-0 doing actual work rather than sitting in a
        docstring.
        """
        if policy.max_verify_age_seconds is None:
            return True
        if self.restore_verified_at is None:
            return False
        return (now - self.restore_verified_at) <= policy.max_verify_age_seconds


@dataclass(frozen=True)
class Dataset:
    """Something worth protecting, and everywhere it currently lives."""

    name: str
    #: A caller-defined class name. Valid exactly when the caller's
    #: policy table has an entry for it -- see Policy's docstring for why
    #: this module ships no class list of its own.
    criticality: str
    copies: tuple[Copy, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Coverage:
    """The verdict for one dataset, with every unmet requirement named."""

    dataset: str
    criticality: str
    counted_copies: int
    unverified_copies: int
    media: tuple[str, ...]
    gaps: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.gaps


def assess(dataset: Dataset, policy: Policy, now: float) -> Coverage:
    """Assess one dataset against its policy.

    Every gap is stated in terms of what is MISSING and what would fix
    it, because "not compliant" is not actionable and a report nobody can
    act on is a report nobody reads.
    """
    counted = [c for c in dataset.copies if c.counts(policy, now)]
    unverified = [c for c in dataset.copies if not c.counts(policy, now)]
    media = tuple(sorted({c.medium for c in counted}))
    gaps: list[str] = []

    if len(counted) < policy.min_copies:
        detail = f"have {len(counted)} verified copies, need {policy.min_copies}"
        if unverified:
            # The most common and most dangerous case: it LOOKS backed
            # up. Say so explicitly rather than only reporting a count.
            detail += (
                f" -- {len(unverified)} further copy/copies exist but are NOT "
                f"restore-verified, so they are beliefs rather than backups"
            )
        gaps.append(detail)

    if len(media) < policy.min_media:
        gaps.append(
            f"on {len(media)} medium/media {media or '()'}, need "
            f"{policy.min_media} distinct -- one failure mode should not "
            f"take every copy"
        )

    if policy.require_offsite and not any(c.offsite for c in counted):
        gaps.append(
            "no verified OFF-SITE copy -- fire, theft or flood takes every "
            "copy in one room"
        )

    if policy.require_immutable and not any(c.immutable for c in counted):
        gaps.append(
            "no verified OFFLINE or IMMUTABLE copy -- compromised credentials "
            "or ransomware can delete everything reachable online"
        )

    return Coverage(
        dataset=dataset.name,
        criticality=dataset.criticality,
        counted_copies=len(counted),
        unverified_copies=len(unverified),
        media=media,
        gaps=tuple(gaps),
    )


def assess_all(
    datasets: Sequence[Dataset],
    now: float,
    policies: dict[str, Policy],
) -> tuple[Coverage, ...]:
    """Assess many datasets, worst first.

    ``policies`` is required rather than defaulted. A silent default here
    would be this module inventing an operator's retention policy and
    then reporting compliance against it -- a green report measured
    against a standard nobody chose is worse than no report.

    Ordered by unmet-requirement count so the report opens on what is
    least protected rather than on whatever happened to be first.
    """
    results = []
    for dataset in datasets:
        policy = policies.get(dataset.criticality)
        if policy is None:
            raise KeyError(
                f"{dataset.name}: no policy supplied for class "
                f"{dataset.criticality!r} (known: {sorted(policies)}) -- "
                f"refusing to guess how hard to protect it"
            )
        results.append(assess(dataset, policy, now))
    return tuple(sorted(results, key=lambda c: (-len(c.gaps), c.dataset)))

# EOF
