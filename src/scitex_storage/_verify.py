#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_verify.py
"""Read the destination back before removing the source.

A COPY IS NOT A MIGRATION UNTIL THE DESTINATION IS READ BACK. That rule was
written on this project's own cards after the 2026-07-22 ywata-note-win
incident, and ``archive`` did not implement it: it called
``shutil.rmtree(source)`` on the strength of rsync's EXIT CODE alone. With
rsync's default quick check (size + mtime, no ``--checksum``), a file that
transferred corrupt while keeping its size and mtime is accepted -- and then
the only copy is deleted.

THE HARD-WON PART IS NOT "COMPARE THE COUNTS", IT IS THE BASELINE.

Archiving ``proj/to_nas`` on 2026-07-23 produced a pre-registered prediction
of 322,183 members and an actual 342,677 -- 20,492 MORE, not 2 fewer. The
socket adjustment was right; the BASELINE was wrong. ``find -type f`` counts
regular files only, while the transport also writes SYMLINKS. The prediction
was made from a narrower population than the one being written.

So the baseline must be EVERY ENTRY THE TRANSPORT WILL WRITE, and any
deviation must be EXPLAINED EXACTLY rather than tolerated:

* a fudge factor ("within 1%") would have swallowed that 20,492 and also
  swallows a genuinely truncated transfer;
* a deviation that makes the result look BETTER (more members than expected)
  is still a deviation and still requires an explanation -- it is evidence
  the baseline is wrong, which means the check is not measuring what it
  claims.

Hence :func:`verify_transfer` takes the shortfall it EXPECTS (sockets and
fifos, which tar/rsync cannot represent) as an explicit argument with a
stated reason, and treats anything else as unexplained.

THREE STATES, as everywhere else in this package. A remote probe that could
not run is ``ok=None`` -- NOT ``ok=False`` and emphatically not ``ok=True``.
The caller must refuse to delete on ``None`` just as firmly as on ``False``:
"I could not check" and "the check failed" have the same consequence for a
destructive action, while being different facts for a human reading the log.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The destination matched what was expected; a source removal is licensed.
VERIFIED = "verified"
#: The destination demonstrably does not match. Never remove the source.
MISMATCH = "mismatch"
#: The probe did not run or returned nothing usable. Never remove the source.
COULD_NOT_LOOK = "could-not-look"

_VERDICTS = (VERIFIED, MISMATCH, COULD_NOT_LOOK)


@dataclass(frozen=True)
class RemoteTally:
    """What a probe observed at the destination.

    ``entry_count`` and ``size_bytes`` are ``None`` when the probe could not
    produce them. ``None`` is not zero: zero is a measurement ("the
    destination is empty", which for a non-empty source is a MISMATCH and a
    very loud one), whereas ``None`` means nothing was learned.
    """

    entry_count: int | None
    size_bytes: int | None
    detail: str = ""


@dataclass(frozen=True)
class TransferVerdict:
    """A fixed-shape answer, with the numbers that produced it retained.

    Every field is present on every call so a caller never guesses which key
    exists. ``expected_count`` is the baseline AFTER the explained shortfall
    has been subtracted, so the recorded numbers are the ones actually
    compared rather than ones the reader must re-derive.
    """

    verdict: str
    expected_count: int | None
    observed_count: int | None
    expected_bytes: int | None
    observed_bytes: int | None
    evidence: str

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise ValueError(
                f"verdict {self.verdict!r} is not one of {_VERDICTS}"
            )
        if not self.evidence.strip():
            raise ValueError(
                "refusing a verdict with no evidence -- a verdict that "
                "cannot be audited is not a measurement"
            )

    @property
    def may_remove_source(self) -> bool:
        """Only a positive verification licenses deleting the original.

        Written as an explicit property rather than left to each call site,
        because ``verdict != MISMATCH`` is the natural-looking test and it is
        WRONG: it treats COULD_NOT_LOOK as permission.
        """
        return self.verdict == VERIFIED


def verify_transfer(
    expected_count: int,
    expected_bytes: int,
    observed: RemoteTally,
    explained_shortfall: int = 0,
    shortfall_reason: str = "",
) -> TransferVerdict:
    """Compare a destination tally against the source baseline.

    ``explained_shortfall`` is the number of source entries the transport
    CANNOT write (sockets, fifos), and it must come with a
    ``shortfall_reason``: an unexplained allowance is indistinguishable from
    a fudge factor, and a fudge factor is how a truncated transfer passes.

    Byte totals are compared as a lower bound rather than for equality --
    the destination filesystem may differ in block size, and rsync may or
    may not preserve sparseness -- so a destination holding FEWER bytes than
    the source is a mismatch, while more is not. The entry count is the
    exact check; bytes catch the "right number of files, all truncated"
    case that a count alone cannot see.
    """
    if explained_shortfall and not shortfall_reason.strip():
        raise ValueError(
            f"refusing an unexplained shortfall allowance of "
            f"{explained_shortfall} -- an allowance with no stated reason is "
            f"a fudge factor, and a fudge factor is how a truncated transfer "
            f"passes verification"
        )
    if explained_shortfall < 0:
        raise ValueError("explained_shortfall cannot be negative")

    baseline = expected_count - explained_shortfall

    if observed.entry_count is None or observed.size_bytes is None:
        return TransferVerdict(
            verdict=COULD_NOT_LOOK,
            expected_count=baseline,
            observed_count=observed.entry_count,
            expected_bytes=expected_bytes,
            observed_bytes=observed.size_bytes,
            evidence=(
                f"destination probe produced no usable tally "
                f"({observed.detail or 'no detail'}) -- the source must NOT "
                f"be removed on an unanswered check"
            ),
        )

    if observed.entry_count != baseline:
        delta = observed.entry_count - baseline
        direction = "more" if delta > 0 else "fewer"
        return TransferVerdict(
            verdict=MISMATCH,
            expected_count=baseline,
            observed_count=observed.entry_count,
            expected_bytes=expected_bytes,
            observed_bytes=observed.size_bytes,
            evidence=(
                f"destination holds {observed.entry_count} entries, expected "
                f"{baseline} ({abs(delta)} {direction}). "
                + (
                    f"Allowed shortfall was {explained_shortfall} "
                    f"({shortfall_reason}). "
                    if explained_shortfall
                    else ""
                )
                + "An unexplained deviation is not a rounding error -- it "
                "means the baseline or the transfer is wrong, and either way "
                "the source must NOT be removed until it is accounted for "
                "exactly. Note a SURPLUS is equally disqualifying: it is "
                "evidence the baseline counted a narrower population than "
                "the transport writes."
            ),
        )

    if observed.size_bytes < expected_bytes:
        return TransferVerdict(
            verdict=MISMATCH,
            expected_count=baseline,
            observed_count=observed.entry_count,
            expected_bytes=expected_bytes,
            observed_bytes=observed.size_bytes,
            evidence=(
                f"entry count matches ({baseline}) but the destination holds "
                f"{observed.size_bytes} bytes against the source's "
                f"{expected_bytes} -- the right number of files, short by "
                f"{expected_bytes - observed.size_bytes} bytes, which is what "
                f"a truncated transfer looks like to a count-only check"
            ),
        )

    return TransferVerdict(
        verdict=VERIFIED,
        expected_count=baseline,
        observed_count=observed.entry_count,
        expected_bytes=expected_bytes,
        observed_bytes=observed.size_bytes,
        evidence=(
            f"destination read back: {observed.entry_count} entries "
            f"(expected {baseline})"
            + (
                f" after an explained shortfall of {explained_shortfall} "
                f"({shortfall_reason})"
                if explained_shortfall
                else ""
            )
            + f", {observed.size_bytes} bytes >= source's {expected_bytes}"
        ),
    )


#: Count every non-directory entry -- regular files, symlinks (including
#: symlinks to directories, which rsync -a writes as symlinks), devices.
#: `-type f` is the wrong baseline and cost a false alarm on 2026-07-23.
REMOTE_TALLY_CMD = (
    "find {path} ! -type d -printf x 2>/dev/null | wc -c; "
    "du -sb {path} 2>/dev/null | cut -f1"
)


def local_tally(path: str) -> RemoteTally:
    """Tally the SOURCE with exactly the semantics :data:`REMOTE_TALLY_CMD` uses.

    This exists so both sides of the comparison are measured the same way.
    Reusing ``ArchivePlan.file_count`` here would be a category error: that
    count comes from ``_scan._measure_dir``, which counts regular files plus
    symlinks whose target is NOT a directory, deliberately excluding
    symlinks-to-directories because it is modelling INODE USAGE. But
    ``rsync -a`` writes a symlink-to-a-directory as a symlink, so the
    transport writes an entry the inode model does not count -- and the
    destination would then show a surplus, failing verification on a
    perfectly good archive.

    That is the 2026-07-23 to_nas mistake in a different costume: a baseline
    drawn from a population narrower than the one the transport writes. The
    fix is not a tolerance, it is measuring the right population.

    Symlinks are never followed (``os.walk(followlinks=False)``), so a
    symlink to a directory is counted once as an entry and never descended.
    """
    import os

    entries = 0
    total = 0
    try:
        for root, dirnames, filenames in os.walk(path, followlinks=False):
            for name in filenames:
                full = os.path.join(root, name)
                entries += 1
                try:
                    total += os.lstat(full).st_size
                except OSError:
                    pass
            # A symlink pointing at a directory lands in dirnames, not
            # filenames -- count it as an entry and do not descend.
            keep: list[str] = []
            for name in dirnames:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    entries += 1
                    try:
                        total += os.lstat(full).st_size
                    except OSError:
                        pass
                else:
                    keep.append(name)
            dirnames[:] = keep
    except OSError as exc:
        return RemoteTally(
            entry_count=None, size_bytes=None, detail=f"local walk failed: {exc}"
        )
    return RemoteTally(entry_count=entries, size_bytes=total)


def parse_remote_tally(stdout: str) -> RemoteTally:
    """Parse :data:`REMOTE_TALLY_CMD` output into a tally.

    Anything unparseable becomes ``None`` rather than 0. A probe that
    silently degrades to zero would report an empty destination, which the
    comparator would correctly call a MISMATCH -- but for the wrong reason,
    and the operator would go looking for a transfer failure that never
    happened.
    """
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return RemoteTally(
            entry_count=None,
            size_bytes=None,
            detail=f"expected 2 output lines, got {len(lines)}: {stdout!r}",
        )
    try:
        count = int(lines[0])
    except ValueError:
        count = None
    try:
        size = int(lines[1])
    except ValueError:
        size = None
    detail = "" if count is not None and size is not None else f"unparseable: {stdout!r}"
    return RemoteTally(entry_count=count, size_bytes=size, detail=detail)

# EOF
