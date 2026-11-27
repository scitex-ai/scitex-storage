#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_archive_records.py
"""The records ``archive`` reads and writes, and the per-unit hashing helpers.

Split out of ``_archive.py`` when single-file units pushed it past the line
limit. Everything here is data and pure measurement: the plan, the manifest
(the source of truth for restore, scrub, retire and retention), the unit
kinds, and the helpers that hash or tally a unit with the population the
transport sends. Nothing here talks to a remote host or removes anything;
that stayed in ``_archive.py``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._archive_transport import _manifest_dir
from ._content_verify import (
    ContentManifest,
    digest_file_unit,
    digest_tree,
    tree_digest,
)
from ._verify import RemoteTally, local_tally

#: ``source_kind`` values. A DIRECTORY is archived as its contents under
#: ``remote_path``; a single regular FILE is archived as the one entry of the
#: directory ``remote_path`` (``remote_path/<file name>``), so every remote
#: probe (tally, digest, scrub) keeps operating on a directory and the remote
#: layout is uniform.
SOURCE_KIND_DIRECTORY = "directory"
SOURCE_KIND_FILE = "file"


@dataclass
class ArchivePlan:
    """The result of :func:`plan_archive` — never touches the network."""

    source: Path
    destination: str
    remote_path: str
    size_bytes: int
    file_count: int
    manifest_path: Path
    #: SOURCE_KIND_DIRECTORY or SOURCE_KIND_FILE. Defaulted so callers that
    #: build an ArchivePlan by hand keep the directory semantics.
    source_kind: str = SOURCE_KIND_DIRECTORY


@dataclass
class ArchiveManifest:
    """Persisted record of one completed archive — the source of truth for restore."""

    source: str
    destination: str
    remote_path: str
    size_bytes: int
    file_count: int
    checksummed: bool
    archived_at: float
    #: The destination read-back verdict ("verified" / "mismatch" /
    #: "could-not-look"). Recorded even when it BLOCKS the source removal,
    #: so a failed verification leaves an auditable record rather than only
    #: an exception in someone's terminal. Defaulted so manifests written by
    #: earlier versions still load via from_dict.
    verified: str = "could-not-look"
    verification_evidence: str = "written by a version that did not verify"
    #: Actual verification instrument. Moves require independent SHA256
    #: ("content"); copy-only operations may use count and byte total ("tally").
    #: Older manifests retain "unknown" rather than inventing past evidence.
    verification_method: str = "unknown"
    #: Remote path of the manifest SIDECAR replica written next to the
    #: archived tree (see ``manifest_sidecar_remote_path``). Recorded even
    #: when the replica FAILED -- a missing map must be visible, not silent.
    #: Defaulted so older manifests still load via from_dict.
    sidecar_path: str = ""
    #: Whether the sidecar round trip (scp + read-back compare) succeeded.
    sidecar_written: bool = False
    #: Human-readable sidecar outcome, success or failure reason.
    sidecar_detail: str = "sidecar not attempted by this version"
    #: Tier of the destination host (canonical :mod:`.._tiers` facts).
    #: Defaulted so older manifests still load via from_dict.
    destination_tier: str = "unknown"
    #: Canonical pre-copy tree digest. Always recorded for verified moves;
    #: a tally-only copy has no content baseline and must not claim one.
    content_digest: str = ""
    #: True for copy-only backups and restorable staged originals.
    #: Older manifests retain their existing interpretation.
    retained_source: bool = False
    #: Where the source was staged when this run retired it instead of
    #: deleting it (restorable retention). Empty when the source was deleted
    #: or kept in place. Defaulted so older manifests still load via
    #: from_dict.
    staged_path: str = ""
    #: Audit of a later ``retire`` run that deleted the retained source after
    #: re-verifying this archive (status, verifier results, bytes freed).
    #: Written before AND after the delete. Empty when never retired.
    #: Defaulted so older manifests still load via from_dict.
    retirement: dict = field(default_factory=dict)
    #: SOURCE_KIND_DIRECTORY or SOURCE_KIND_FILE. For a file, the remote copy
    #: is ``remote_path/<source name>`` and ``content_digest`` is the file's
    #: own sha256 (the same value ``_retention._snapshot`` computes), not a
    #: tree digest. Defaulted so older manifests still load via from_dict.
    source_kind: str = SOURCE_KIND_DIRECTORY

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ArchiveManifest:
        return cls(**data)


def remote_tree_digest(manifest: ArchiveManifest) -> str:
    """The tree digest a re-hash of ``manifest.remote_path`` must reproduce.

    For a directory this is ``content_digest`` itself. For a single file the
    remote directory holds exactly one entry, named after the source, whose
    sha256 is ``content_digest``. Empty when there is no content baseline.
    """
    if not manifest.content_digest:
        return ""
    if manifest.source_kind != SOURCE_KIND_FILE:
        return manifest.content_digest
    name = Path(manifest.source).name
    return tree_digest(ContentManifest(digests={name: manifest.content_digest}))


def _source_manifest(plan: ArchivePlan, exclude: tuple[str, ...]) -> ContentManifest:
    """Hash the source with the population the transport sends."""
    if plan.source_kind == SOURCE_KIND_FILE:
        return digest_file_unit(str(plan.source))
    return digest_tree(str(plan.source), exclude=exclude)


def _source_tally(plan: ArchivePlan, exclude: tuple[str, ...]) -> RemoteTally:
    """Count and size the source with :data:`REMOTE_TALLY_CMD` semantics."""
    if plan.source_kind != SOURCE_KIND_FILE:
        return local_tally(str(plan.source), exclude=exclude)
    try:
        info = plan.source.lstat()
    except OSError as exc:
        return RemoteTally(entry_count=None, size_bytes=None, detail=str(exc))
    return RemoteTally(entry_count=1, size_bytes=info.st_size)


def _unit_digest(plan: ArchivePlan, manifest: ContentManifest) -> str:
    """Canonical content digest: the file's sha256, or the tree digest."""
    if plan.source_kind == SOURCE_KIND_FILE:
        return manifest.digests.get(plan.source.name, "")
    return tree_digest(manifest)


def iter_manifests() -> list[ArchiveManifest]:
    """All readable local manifests, newest first; unreadable files skipped.

    A corrupt JSON next to real manifests must not break listing -- it is
    skipped silently here and surfaced by nothing, because a half-written
    manifest from a killed run is routine, not an event. Sort key is the
    recorded ``archived_at``, not mtime: the file may have been rewritten
    (sidecar status) after the archive it records.
    """
    found: list[ArchiveManifest] = []
    try:
        paths = sorted(_manifest_dir().glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found.append(ArchiveManifest.from_dict(data))
        except (OSError, ValueError, TypeError):
            continue
    found.sort(key=lambda m: m.archived_at, reverse=True)
    return found


# EOF
