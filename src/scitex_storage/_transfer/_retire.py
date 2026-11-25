#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retire a retained source whose archive is already content-verified.

``backup`` (``apply_archive(remove_source=False)``) copies a tree, proves the
copy by SHA256, writes a manifest with the canonical ``content_digest`` and
KEEPS the source. Nothing in the package could later act on that proof:
``archive`` re-copies and then stages the original next to itself for seven
days, which frees nothing on a full filesystem, and ``reclaim`` re-copies to
yet another place. ``retire`` is the missing last step: delete the retained
source, and only the source, once the archive is shown to be intact NOW.

Every gate must pass before anything is deleted, and "could not look"
refuses as firmly as "mismatch":

1. A fresh content scrub of the remote copy reproduces the manifest's
   ``content_digest`` (:func:`._scrub.scrub_manifest`, tally AND content).
2. The local source re-hashes to that same digest, so what is deleted is
   exactly what was archived.
3. Each ``--also`` sibling (a hard-linked twin such as a snapshot tree) holds
   only regular files whose inodes are files of the source. Deleting a
   sibling then removes links, never data the archive does not hold. Without
   this, removing one of two hard-linked trees frees no space at all.

Nothing is staged: staging on the same filesystem frees nothing, which is
the situation this verb exists for. The audit is written twice, in the
manifest (``retirement``) and in a transfer log, once before the first
unlink (status ``deleting``) and once after (``retired`` with bytes freed).
A refusal is recorded the same way. Planning is read-only and offline.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from ._archive import ArchiveManifest
from ._audit import _entry, _now, _record_path, _write
from ._content_verify import digest_tree, tree_digest
from ._durable import atomic_json
from ._scrub import scrub_manifest
from ._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED


class RetireRefusedError(RuntimeError):
    """A gate did not pass; nothing was deleted."""


@dataclass(frozen=True)
class SiblingProof:
    """Whether a sibling tree is made only of links to source files."""

    path: str
    file_count: int
    verdict: str
    evidence: str


@dataclass
class RetirePlan:
    """What ``retire`` would delete. Computed offline, executes nothing."""

    manifest_path: Path
    manifest: ArchiveManifest
    source: Path
    source_file_count: int
    source_bytes: int
    expected_bytes_freed: int
    siblings: list[SiblingProof] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.refusals

    def to_dict(self) -> dict:
        return {
            "manifest_path": str(self.manifest_path),
            "source": str(self.source),
            "destination": self.manifest.destination,
            "remote_path": self.manifest.remote_path,
            "content_digest": self.manifest.content_digest,
            "source_file_count": self.source_file_count,
            "source_bytes": self.source_bytes,
            "expected_bytes_freed": self.expected_bytes_freed,
            "siblings": [vars(s) for s in self.siblings],
            "refusals": list(self.refusals),
            "ready": self.ready,
        }


def _load_manifest(path: Path) -> ArchiveManifest:
    try:
        return ArchiveManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"not a readable archive manifest: {path} ({exc})") from exc


def _entries(root: Path):
    """Yield (path, lstat) for every non-directory entry; walk errors raise.

    Same population as ``digest_tree``: files, symlinks, and symlinks to
    directories (recorded, never descended).
    """

    def failed(exc: OSError) -> None:
        raise exc

    for parent, dirs, names in os.walk(root, onerror=failed):
        linked = [d for d in dirs if os.path.islink(os.path.join(parent, d))]
        for name in names + linked:
            full = os.path.join(parent, name)
            yield full, os.lstat(full)


def _index_source(source: Path) -> tuple[dict[tuple[int, int], list[int]], int]:
    """Map (dev, ino) -> [size, nlink, links seen] for regular source files."""
    inodes: dict[tuple[int, int], list[int]] = {}
    count = 0
    for _full, info in _entries(source):
        count += 1
        if stat.S_ISREG(info.st_mode):
            record = inodes.setdefault(
                (info.st_dev, info.st_ino), [info.st_size, info.st_nlink, 0]
            )
            record[2] += 1
    return inodes, count


def _prove_sibling(sibling: Path, source: Path, inodes: dict) -> SiblingProof:
    """Every non-directory entry of ``sibling`` must be a link to a source file."""
    path = str(sibling)
    if sibling.is_symlink() or not sibling.is_dir():
        return SiblingProof(path, 0, COULD_NOT_LOOK, "not a real directory")
    real, src = sibling.resolve(), source.resolve()
    if real == src or src in real.parents or real in src.parents:
        return SiblingProof(path, 0, MISMATCH, "overlaps the source tree")
    if sibling.stat().st_dev != source.stat().st_dev:
        return SiblingProof(
            path, 0, MISMATCH, "on a different filesystem than the source"
        )
    count = 0
    seen: list[tuple[int, int]] = []
    try:
        for full, info in _entries(sibling):
            count += 1
            key = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode):
                return SiblingProof(
                    path, count, MISMATCH, f"not a regular file: {full}"
                )
            if key not in inodes:
                return SiblingProof(
                    path, count, MISMATCH, f"not a hard link to a source file: {full}"
                )
            seen.append(key)
    except OSError as exc:
        return SiblingProof(path, count, COULD_NOT_LOOK, f"{type(exc).__name__}: {exc}")
    for key in seen:
        inodes[key][2] += 1
    return SiblingProof(
        path, count, VERIFIED, f"all {count} entries are hard links to source files"
    )


def plan_retire(manifest_path: str | Path, also: tuple[str, ...] = ()) -> RetirePlan:
    """Check the manifest and siblings offline; hash nothing, delete nothing."""
    manifest_path = Path(manifest_path).expanduser()
    manifest = _load_manifest(manifest_path)
    source = Path(manifest.source)
    refusals: list[str] = []
    if manifest.verified != VERIFIED or manifest.verification_method != "content":
        refusals.append("manifest does not record a content-verified archive")
    if not manifest.content_digest:
        refusals.append("manifest has no content_digest baseline")
    if manifest.staged_path:
        refusals.append(f"source was already staged at {manifest.staged_path}")
    if manifest.retirement.get("status") == "retired":
        refusals.append("source was already retired")
    if source.is_symlink() or not source.is_dir():
        refusals.append(f"source is not a directory: {source}")
        return RetirePlan(manifest_path, manifest, source, 0, 0, 0, [], refusals)
    try:
        inodes, files = _index_source(source)
    except OSError as exc:
        refusals.append(f"source could not be walked: {exc}")
        return RetirePlan(manifest_path, manifest, source, 0, 0, 0, [], refusals)
    resolved = [Path(p).expanduser().resolve() for p in also]
    for i, a in enumerate(resolved):
        if any(a == b or a in b.parents or b in a.parents for b in resolved[i + 1 :]):
            refusals.append(f"sibling {a} overlaps another --also sibling")
    siblings = [_prove_sibling(Path(p).expanduser(), source, inodes) for p in also]
    refusals += [
        f"sibling {s.path}: {s.verdict} ({s.evidence})"
        for s in siblings
        if s.verdict != VERIFIED
    ]
    total = sum(size for size, _, _ in inodes.values())
    freed = sum(size for size, nlink, seen in inodes.values() if seen >= nlink)
    return RetirePlan(
        manifest_path, manifest, source, files, total, freed, siblings, refusals
    )


def _free_bytes(path: Path) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def apply_retire(plan: RetirePlan, *, runner=None, cancelled=None) -> dict:
    """Re-verify everything, then delete siblings and source. Returns the audit."""
    log_path = _record_path()
    log = _entry(
        "retire", plan.source, plan.manifest.destination, plan.manifest.remote_path
    )
    log.update(
        status="running", stage="preflight", manifest_path=str(plan.manifest_path)
    )
    audit = {"status": "verifying", "started_at": _now(), "transfer_log": str(log_path)}

    def record(**changes) -> None:
        audit.update(changes)
        plan.manifest.retirement = dict(audit)
        atomic_json(plan.manifest_path, plan.manifest.to_dict())
        log.update(retirement_status=audit["status"])
        _write(log_path, log)

    def refuse(stage: str, reason: str) -> None:
        log.update(status="refused", stage=stage, reason=reason, finished_at=_now())
        record(status="refused", refused_at=_now(), reason=reason)
        raise RetireRefusedError(f"{reason} -- nothing was deleted.")

    _write(log_path, log)
    if not plan.ready:
        refuse("preflight", "; ".join(plan.refusals))

    log["stage"] = "scrub"
    scrub = scrub_manifest(plan.manifest, content=True, runner=runner)
    audit["scrub"] = scrub.to_dict()
    if scrub.tally_verdict != VERIFIED or scrub.content_verdict != VERIFIED:
        refuse(
            "scrub",
            f"remote copy not verified (tally {scrub.tally_verdict}, content {scrub.content_verdict})",
        )

    log["stage"] = "source_digest"
    local = digest_tree(str(plan.source), cancelled=cancelled)
    local_digest = tree_digest(local) if local.usable and not local.unreadable else ""
    audit["source_digest"] = {"entries": len(local.digests), "digest": local_digest}
    if local_digest != plan.manifest.content_digest:
        refuse(
            "source_digest",
            "local source does not reproduce the manifest content_digest",
        )

    log["stage"] = "sibling_inodes"
    fresh = plan_retire(plan.manifest_path, tuple(s.path for s in plan.siblings))
    audit["siblings"] = [vars(s) for s in fresh.siblings]
    if not fresh.ready:
        refuse("sibling_inodes", "; ".join(fresh.refusals))

    parent = plan.source.parent
    before = _free_bytes(parent)
    log.update(stage="delete")
    record(
        status="deleting",
        free_bytes_before=before,
        expected_bytes_freed=fresh.expected_bytes_freed,
    )
    try:
        for sibling in fresh.siblings:
            shutil.rmtree(sibling.path)
        shutil.rmtree(plan.source)
    except BaseException as exc:
        log.update(status="failed", error_type=type(exc).__name__, finished_at=_now())
        record(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    after = _free_bytes(parent)
    log.update(status="completed", stage="complete", finished_at=_now())
    record(
        status="retired",
        retired_at=time.time(),
        free_bytes_after=after,
        bytes_freed=after - before,
    )
    return dict(audit)


# EOF
