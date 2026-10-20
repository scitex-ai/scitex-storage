#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_scrub.py
"""Re-verify an archived tree against its manifest, months later.

An archive is verified ONCE, at migration time. After that the cold copy
sits on a NAS while disks age, firmware updates and other hands touch the
share -- and the next read is the recall, exactly when a failure hurts
most. Scrub answers "is the cold copy still what the manifest says" on
demand, WITHOUT needing the source (usually long deleted, which is the
entire point of reading the manifest instead of a tree).

Read-only, always: scrub never writes to the destination and never touches
a source. It licenses nothing destructive, so its verdicts inform rather
than permit -- but they use the same three-state vocabulary
(verified / mismatch / could-not-look) so a reader never re-learns it.
"""

from __future__ import annotations

from dataclasses import dataclass

from scitex_ssh import exec_remote

from ._archive import ArchiveManifest
from ._archive_transport import _quote_remote_path
from ._content_verify import tree_digest
from ._remote_digest import REMOTE_DIGEST_CMD, parse_remote_manifest
from ._verify import (
    COULD_NOT_LOOK,
    MISMATCH,
    REMOTE_TALLY_CMD,
    VERIFIED,
    RemoteTally,
    TransferVerdict,
    parse_remote_tally,
    verify_transfer,
)


@dataclass(frozen=True)
class ScrubReport:
    """One cold re-verification, tally always and content when possible."""

    manifest_source: str
    destination: str
    remote_path: str
    tally: TransferVerdict
    #: verified / mismatch / could-not-look / unknown (no baseline recorded).
    content: str
    content_evidence: str

    @property
    def tally_verdict(self) -> str:
        """The tally verdict as a plain string (auditor-friendly accessor)."""
        return self.tally.verdict

    @property
    def content_verdict(self) -> str:
        """The content verdict as a plain string."""
        return self.content

    def to_dict(self) -> dict:
        """JSON-ready mapping (every field present on every call)."""
        return {
            "manifest_source": self.manifest_source,
            "destination": self.destination,
            "remote_path": self.remote_path,
            "tally_verdict": self.tally_verdict,
            "tally_evidence": self.tally.evidence,
            "observed_count": self.tally.observed_count,
            "observed_bytes": self.tally.observed_bytes,
            "content_verdict": self.content_verdict,
            "content_evidence": self.content_evidence,
        }


def _unusable_reason(remote) -> str:
    """One-line summary of why a remote content manifest is unusable."""
    if remote.root_missing:
        return "remote root is missing"
    if remote.unreadable:
        first = next(iter(remote.unreadable))
        return f"unreadable entries remain ({first} + {len(remote.unreadable) - 1} more)"
    return "no usable digests"


def scrub_manifest(
    manifest: ArchiveManifest,
    *,
    content: bool = False,
    runner=None,
) -> ScrubReport:
    """Re-measure the archived tree and compare it with ``manifest``.

    Tally compare ALWAYS runs (entry count + bytes vs the manifest's
    recorded numbers). The content re-hash runs only when asked AND the
    manifest carries a ``content_digest`` baseline; otherwise content
    reports ``unknown`` -- a tally-cleared archive has no baseline to
    reproduce, and manufacturing one from the destination would compare
    the destination with itself.
    """
    probe = exec_remote(
        manifest.destination,
        REMOTE_TALLY_CMD.format(path=_quote_remote_path(manifest.remote_path)),
        runner=runner,
    )
    observed = (
        parse_remote_tally(probe.stdout)
        if probe.success
        else RemoteTally(
            entry_count=None,
            size_bytes=None,
            detail=f"remote tally exited {probe.returncode}: {probe.stderr.strip()}",
        )
    )
    tally = verify_transfer(
        expected_count=manifest.file_count,
        expected_bytes=manifest.size_bytes,
        observed=observed,
    )

    content_verdict = "unknown"
    content_evidence = (
        "no content baseline recorded -- this archive was cleared by tally; "
        "re-run archive with --verify-content to record one"
    )
    if content and manifest.content_digest:
        digest_probe = exec_remote(
            manifest.destination,
            REMOTE_DIGEST_CMD.format(path=_quote_remote_path(manifest.remote_path)),
            runner=runner,
        )
        remote = parse_remote_manifest(
            digest_probe.stdout, probe_succeeded=digest_probe.success
        )
        if not digest_probe.success or not remote.usable:
            content_verdict = COULD_NOT_LOOK
            content_evidence = (
                "remote content probe did not run to completion; "
                + _unusable_reason(remote)
            )
        elif tree_digest(remote) == manifest.content_digest:
            content_verdict = VERIFIED
            content_evidence = (
                "remote tree digest reproduces the manifest baseline "
                f"({len(remote.digests)} entries re-hashed)"
            )
        else:
            content_verdict = MISMATCH
            content_evidence = (
                "remote tree digest differs from the manifest baseline: "
                "the cold copy changed since archiving"
            )

    return ScrubReport(
        manifest_source=manifest.source,
        destination=manifest.destination,
        remote_path=manifest.remote_path,
        tally=tally,
        content=content_verdict,
        content_evidence=content_evidence,
    )
