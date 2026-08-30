#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_content_verify.py
"""Compare destination CONTENT against the source, not a tally of it.

WHY THIS EXISTS ALONGSIDE :mod:`._verify` RATHER THAN REPLACING IT.

``verify_transfer`` compares an entry COUNT and a byte TOTAL. That is the
right check to gate PROGRESS on: it is cheap, it runs over a whole tree in
seconds, and it catches the failures that actually dominate -- a transfer
that stopped early, a destination that was never written, the truncation
case its own docstring names.

It is the wrong check to gate a DELETE on, and the module says so about
itself: bytes are there to catch "the right number of files, all
truncated", which is an admission that the byte total is a PROXY. A proxy
answers a question about itself; the operator is asking a question about the
world. Count and size cannot see:

* a file of exactly the right length whose contents are wrong;
* a write that was truncated and then padded to length;
* bit rot on the destination medium, which changes no size and no count;
* two files whose contents were swapped -- count identical, bytes identical.

Every one of those passes ``verify_transfer`` and then licenses
``shutil.rmtree`` on the only remaining copy.

The operator's requirement is explicit and is the same rule:

    「照合は内容ハッシュ。名前でもサイズでも日付でもなく sha256」
    (verify by content hash -- not by name, not by size, not by date)

THE GENERAL FORM, which cost us twice this week in two different layers:
a destructive step must never read a proxy. scitex-db measured
``pg_stat_user_tables`` reporting a table as 0 rows while it held 2,042 --
an ESTIMATE is a measurement of the estimator, not of the table. Size and
mtime are the filesystem's estimate of "same file". Content or nothing.

SO: TALLY GATES PROGRESS, CONTENT GATES DELETION. Both, not either. Hashing
9.1 TB twice is hours of I/O and would be absurd as a routine progress check;
skipping it before an irreversible delete is how the 2026-07-22 incident
happened. Two checks with two jobs.

BOTH RETURN THE SAME :class:`._verify.TransferVerdict`. A caller that already
knows how to read a verdict learns no new type, and
``verdict.may_remove_source`` keeps being the single place that decides.

THREE STATES, and the middle one is the whole point. A file that cannot be
READ is not a mismatch and is emphatically not a pass: it is
``COULD_NOT_LOOK``, and it poisons the whole verdict. A permission error on
one file out of 400,000 must not be rounded away, because "I could not check
this one" and "this one is fine" differ by exactly the file you are about to
delete.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from ._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED, TransferVerdict

#: Read size for hashing. Large enough that syscall overhead is irrelevant on
#: spinning disks and network filesystems, small enough to stay off the large
#: object heap. Not tunable on purpose: a knob here invites someone to tune it
#: per-call, and then two manifests of the same tree are no longer comparable
#: as artefacts even though the digests would in fact match.
_CHUNK = 1024 * 1024

#: Marks a symlink entry in the manifest. The digest of a symlink is the
#: digest of its TARGET STRING, never of the file it points at.
#:
#: Following symlinks would be wrong three separate ways: it double-counts a
#: file that is also present in the tree, it can escape the tree entirely
#: (``../../etc/passwd``), and it turns a broken symlink -- which is a
#: perfectly legitimate thing to migrate -- into an unreadable file and hence
#: a could-not-look over the entire run. What we are verifying is that the
#: destination holds the same LINK, which is what the transport wrote.
SYMLINK_PREFIX = "symlink:"


@dataclass(frozen=True)
class ContentManifest:
    """Every entry in a tree, keyed by path relative to the tree root.

    ``digests`` maps a relative path to its sha256 hex digest. ``unreadable``
    holds the entries that could not be hashed, WITH the reason -- they are
    kept as their own population rather than dropped, because a dropped
    unreadable file is silently equivalent to a matching one.

    ``root_missing`` distinguishes "the tree is empty" from "the tree is not
    there", which are the same zero to any counter. This is the same guard
    that ``local_tally`` needed and the same one shipped in ``_survey``: an
    unmounted mount point is a readable, error-free, empty directory.
    """

    digests: dict[str, str] = field(default_factory=dict)
    unreadable: dict[str, str] = field(default_factory=dict)
    root_missing: bool = False

    @property
    def usable(self) -> bool:
        """True when this manifest can support a verdict at all.

        A manifest whose root was missing says nothing about content. One
        with unreadable entries says something, but not enough to license a
        delete -- that judgement belongs to :func:`verify_content`, which is
        why this property is deliberately NOT "has no unreadable entries".
        """
        return not self.root_missing


def digest_file(path: str) -> str:
    """sha256 of a single entry, hex.

    A symlink digests its target STRING (see :data:`SYMLINK_PREFIX`).
    Raises ``OSError`` on anything unreadable -- the caller decides whether
    that is fatal, because this function has no idea how many other files
    there are and therefore cannot judge severity.
    """
    if os.path.islink(path):
        return hashlib.sha256(
            (SYMLINK_PREFIX + os.readlink(path)).encode("utf-8", "surrogateescape")
        ).hexdigest()

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def digest_tree(root: str) -> ContentManifest:
    """Hash every non-directory entry under ``root``.

    The population is deliberately IDENTICAL to
    :func:`._verify.local_tally`'s: every non-directory entry, including
    symlinks-to-directories, which ``rsync -a`` writes as symlinks. Using a
    narrower population here would reintroduce the 2026-07-23 ``to_nas``
    mistake -- a baseline drawn from a smaller set than the transport writes
    -- in a module whose entire job is to be the strict check.

    Symlinks are never followed (``os.walk(followlinks=False)``), so a
    symlink to a directory is recorded once as an entry and never descended.
    """
    if not os.path.isdir(root):
        return ContentManifest(root_missing=True)

    digests: dict[str, str] = {}
    unreadable: dict[str, str] = {}

    def record(full: str) -> None:
        rel = os.path.relpath(full, root)
        try:
            digests[rel] = digest_file(full)
        except OSError as exc:
            unreadable[rel] = f"{type(exc).__name__}: {exc}"

    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                record(os.path.join(dirpath, name))
            keep: list[str] = []
            for name in dirnames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    record(full)
                else:
                    keep.append(name)
            dirnames[:] = keep
    except OSError as exc:
        # A walk that dies partway has produced a PARTIAL manifest, and a
        # partial manifest that looks complete is the dangerous artefact
        # here: every file it did not reach would compare as "absent from
        # the source" and could license deleting a destination copy. Fail
        # the whole manifest rather than return the part that worked.
        return ContentManifest(
            digests={},
            unreadable={"<walk>": f"{type(exc).__name__}: {exc}"},
            root_missing=False,
        )

    return ContentManifest(digests=digests, unreadable=unreadable)


def verify_content(source: ContentManifest, destination: ContentManifest) -> TransferVerdict:
    """Compare two manifests and answer whether the SOURCE may be removed.

    Returns the same :class:`._verify.TransferVerdict` as the tally check, so
    ``may_remove_source`` remains the single decision point.

    The counts carried on the verdict are ENTRY counts, and the byte fields
    are ``None``: this check does not measure bytes, and reporting a number
    it did not measure would be worse than reporting nothing. ``None`` here
    means "not measured by this check", consistent with ``RemoteTally``
    where ``None`` is never zero.
    """
    # --- could-not-look cases, evaluated FIRST ------------------------------
    # Order matters. Every one of these must win over a content comparison,
    # because a comparison against an unknown population produces a
    # confident-looking verdict from data that was never read.
    if source.root_missing:
        return TransferVerdict(
            verdict=COULD_NOT_LOOK,
            expected_count=None,
            observed_count=len(destination.digests),
            expected_bytes=None,
            observed_bytes=None,
            evidence=(
                "the SOURCE tree is missing or is not a directory, so there is "
                "no baseline to compare against. This is not 'nothing to "
                "migrate': an unmounted mount point is a readable, error-free, "
                "empty directory, and treating it as an empty source would "
                "license deleting a destination that is in fact the only copy"
            ),
        )

    if destination.root_missing:
        return TransferVerdict(
            verdict=COULD_NOT_LOOK,
            expected_count=len(source.digests),
            observed_count=None,
            expected_bytes=None,
            observed_bytes=None,
            evidence=(
                f"the DESTINATION tree is missing or is not a directory; "
                f"{len(source.digests)} source entries were hashed but nothing "
                f"was read back. The source must NOT be removed on an "
                f"unanswered check"
            ),
        )

    if source.unreadable or destination.unreadable:
        n_src, n_dst = len(source.unreadable), len(destination.unreadable)
        sample = list(source.unreadable.items())[:3] + list(destination.unreadable.items())[:3]
        shown = "; ".join(f"{p} ({why})" for p, why in sample)
        return TransferVerdict(
            verdict=COULD_NOT_LOOK,
            expected_count=len(source.digests),
            observed_count=len(destination.digests),
            expected_bytes=None,
            observed_bytes=None,
            evidence=(
                f"{n_src} source and {n_dst} destination entr"
                f"{'y' if n_src + n_dst == 1 else 'ies'} could not be hashed, "
                f"so this run cannot say whether they match: {shown}. "
                f"An unreadable file is NOT a matching file. Rounding these "
                f"away would license deleting exactly the entries nobody "
                f"verified -- fix the read errors and re-run rather than "
                f"accepting a verdict over a population that was never read"
            ),
        )

    if not source.digests:
        return TransferVerdict(
            verdict=COULD_NOT_LOOK,
            expected_count=0,
            observed_count=len(destination.digests),
            expected_bytes=None,
            observed_bytes=None,
            evidence=(
                "the source walk completed without error but read ZERO "
                "entries, and this check cannot tell an EMPTY tree from an "
                "INVISIBLE one. Zero is the denominator problem in its purest "
                "form: '0 of 0 mismatched' and '0 of 200,000 mismatched' are "
                "the same sentence and only one of them is a result"
            ),
        )

    # --- real comparison ----------------------------------------------------
    src_keys, dst_keys = set(source.digests), set(destination.digests)
    missing = sorted(src_keys - dst_keys)
    surplus = sorted(dst_keys - src_keys)
    differing = sorted(
        k for k in (src_keys & dst_keys) if source.digests[k] != destination.digests[k]
    )

    if missing or surplus or differing:
        parts = []
        if missing:
            parts.append(f"{len(missing)} MISSING at the destination (e.g. {missing[:3]})")
        if differing:
            parts.append(
                f"{len(differing)} present with DIFFERENT CONTENT "
                f"(e.g. {differing[:3]}) -- these are the ones a size-and-count "
                f"check reports as fine"
            )
        if surplus:
            parts.append(
                f"{len(surplus)} EXTRA at the destination (e.g. {surplus[:3]}); "
                f"a surplus is equally disqualifying, because it is evidence "
                f"the source baseline counted a narrower population than the "
                f"transport wrote"
            )
        return TransferVerdict(
            verdict=MISMATCH,
            expected_count=len(src_keys),
            observed_count=len(dst_keys),
            expected_bytes=None,
            observed_bytes=None,
            evidence=(
                f"content comparison over {len(src_keys)} source entries: "
                + "; ".join(parts)
                + ". The source must NOT be removed until every entry is "
                "accounted for exactly"
            ),
        )

    return TransferVerdict(
        verdict=VERIFIED,
        expected_count=len(src_keys),
        observed_count=len(dst_keys),
        expected_bytes=None,
        observed_bytes=None,
        evidence=(
            f"sha256 matched for all {len(src_keys)} entries, with no entry "
            f"missing and none extra at the destination. Every entry was read "
            f"and hashed on both sides; nothing was skipped or estimated"
        ),
    )

# EOF
