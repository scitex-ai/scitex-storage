#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Move-not-delete tiering: archive a directory to nas/nas2, with a manifest + restore.

The MVP verb behind ``scitex-storage archive`` / ``restore``. Built on
scitex-ssh's ``sync_dir`` rsync-over-ssh primitive (per scitex-ssh's own
design: it stays transport-only and policy-free — copy-verify-then-remove
tiering logic belongs here, not in the transport layer).

Design constraints:

* **Copy-verify-then-remove, never delete-then-copy.** :func:`apply_archive`
  only removes the local source after ``sync_dir`` reports success; a
  failed sync leaves the source completely untouched and raises loud.
* **The remote parent must exist before rsync does.** On a destination
  that's never held archived data before, ``~/scitex-storage-archive/...``
  simply doesn't exist yet — rsync then fails with "errors selecting
  input/output files (code 3)". :func:`apply_archive` runs
  ``mkdir -p`` on the remote parent via ``exec_remote`` before ``sync_dir``.
  Deliberately NOT ``rsync --mkpath`` (added in rsync 3.2.3; a real nas2
  target still runs 3.0.7, where ``--mkpath`` is a hard unknown-option
  error) — ``mkdir -p`` works on any rsync version. Found + verified by
  scitex-ssh smoke-testing a real archive against real nas/nas2, not a
  hypothetical (2026-07-11).
* **Never wrap a leading ``~`` in shell quotes.** Every remote path this
  module builds a shell command from (the ``mkdir -p`` above, ``rm -rf``
  in :func:`apply_restore`) goes through :func:`_quote_remote_path`, which
  leaves a leading ``~/`` unquoted and only quotes the rest — a bare
  ``shlex.quote(path)`` turns ``~`` into a literal character (tilde-
  expansion only applies to an unquoted leading ``~``), silently creating
  a directory named ``~`` instead of resolving ``$HOME``. Also found by
  scitex-ssh's real-nas2 smoke test: the stray ``~`` directory was sitting
  in the remote home directory after the naive-quoting version "succeeded"
  (2026-07-11).
* **Trailing slash controls "contents of" vs "the directory itself".**
  rsync's classic gotcha: a source arg with NO trailing slash copies the
  source directory itself into the destination (nesting one level:
  ``dest/<source-basename>/...``); WITH a trailing slash it copies the
  source's *contents* directly into the destination. Both
  :func:`apply_archive` (push) and :func:`apply_restore` (pull) want
  "contents of," so the copy SIDE (``local`` for push, ``remote_path`` for
  pull) always gets a trailing slash appended before being handed to
  ``sync_dir`` -- otherwise a restore lands two directories deep
  (``source/<remote-basename>/<source-basename>/...``) with byte-correct
  but wrongly-placed data, easy to miss if only checksums are checked.
  scitex-ssh's own docstring is explicit that trailing-slash semantics are
  the caller's to control; this is that layer. Found by scitex-ssh
  smoke-testing a real archive+restore round-trip and diffing the actual
  file paths, not just checksums (2026-07-11).
* **Manifest before delete.** The manifest is written *before* the local
  source is removed, so a crash between "sync succeeded" and "manifest
  written" fails safe (source still present; re-running `archive` is a
  cheap no-op rsync).
* **Restore never destroys the archive by default.** :func:`apply_restore`
  pulls the data back to its original local path; the remote copy is only
  removed if the caller explicitly opts in via ``delete_remote=True``.
* **Aliases, not addresses.** ``destination`` is one of the ``~/.ssh/config``
  aliases ``nas`` / ``nas2`` — scitex-ssh already handles mux discipline per
  alias; this module never opens its own ssh connections.
"""

from __future__ import annotations

import json
import posixpath
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scitex_ssh import SSHResult, exec_remote, sync_dir

from .._measure._scan import (
    MissingSystemDependencyError as MissingSystemDependencyError,
)
from .._measure._scan import _measure_dir
from .._measure._space import remote_free_bytes
from .._tiers import tier_of_destination
from ._archive_transport import (
    _UNSAFE_REMOTE_PATHS,
    DESTINATIONS,
    _as_dir_contents,
    _default_remote_path,
    _manifest_dir,
    _manifest_path,
    _quote_remote_path,
    _rsync_binary,
    manifest_sidecar_remote_path,
    resolve_destination,
    write_manifest_sidecar,
)
from ._archive_transport import DEFAULT_REMOTE_ROOT as DEFAULT_REMOTE_ROOT
from ._content_verify import digest_tree, tree_digest, verify_content
from ._durable import atomic_json
from ._remote_digest import REMOTE_DIGEST_CMD, parse_remote_manifest
from ._restore import RestorePlan as RestorePlan
from ._restore import apply_restore as apply_restore
from ._restore import plan_restore as plan_restore
from ._sweep import InsufficientSpaceError, check_space
from ._verify import (
    REMOTE_TALLY_CMD,
    RemoteTally,
    local_tally,
    parse_remote_tally,
    verify_transfer,
)


class ArchiveNotVerifiedError(RuntimeError):
    """The destination could not be confirmed, so the source was NOT removed.

    Its own class so a caller can distinguish "the copy is there but
    unconfirmed" -- recoverable, retryable, nothing lost -- from a transport
    failure mid-push. Both leave the source intact; only one means the data
    already reached the destination.
    """


@dataclass
class ArchivePlan:
    """The result of :func:`plan_archive` — never touches the network."""

    source: Path
    destination: str
    remote_path: str
    size_bytes: int
    file_count: int
    manifest_path: Path


def plan_archive(
    source: str | Path,
    destination: str,
    remote_path: str | None = None,
) -> ArchivePlan:
    """Compute (never execute) an archive plan for ``source`` -> ``destination``.

    Read-only: measures ``source`` with the same walk as ``scan``;
    never touches the network. Fail-loud on a missing / non-directory
    ``source`` or an unrecognised ``destination``.

    Deliberately does NOT require ``rsync``: planning is genuinely
    transport-free, and the ``runner`` seam on :func:`apply_archive` means a
    caller may legitimately plan here and transport by other means. The
    binary is required by the code that actually invokes it (see
    :func:`_rsync_binary`'s call sites) and, for the CLI's benefit, checked
    up-front in ``_cli/_archive_cmd.py`` so the DEFAULT dry-run cannot
    promise a run that could not start.
    """
    # Rewrite a retired alias BEFORE validating, so the plan carries the name
    # that will actually be dialled. The ArchivePlan's `destination` ends up in
    # the manifest, which is what `restore` reads months later -- recording the
    # dead name there would produce a manifest that cannot be restored from.
    destination, _notice = resolve_destination(destination)
    if destination not in DESTINATIONS:
        raise ValueError(
            f"destination must be one of {DESTINATIONS}, got {destination!r}"
        )
    source = Path(source).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"path does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"not a directory: {source}")
    source = source.resolve()

    size, file_count, _newest_mtime, _err = _measure_dir(source)
    remote = remote_path or _default_remote_path(source)
    if remote.strip() in _UNSAFE_REMOTE_PATHS:
        raise ValueError(f"refusing an unsafe remote path: {remote!r}")

    return ArchivePlan(
        source=source,
        destination=destination,
        remote_path=remote,
        size_bytes=size,
        file_count=file_count,
        manifest_path=_manifest_path(source),
    )


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ArchiveManifest:
        return cls(**data)


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


def apply_archive(
    plan: ArchivePlan,
    *,
    checksum: bool = True,
    exclude: tuple[str, ...] = (),
    verify_content_too: bool = False,
    remove_source: bool = True,
    retire_source: bool = True,
    retention_days: float = 7.0,
    runner=None,
) -> ArchiveManifest:
    """Execute an archive plan: push, READ THE DESTINATION BACK, write the
    manifest, and remove the source ONLY on a positive verification.

    ``remove_source=False`` is BACKUP mode: the destination is copied and
    verified exactly the same way, the manifest records ``retained_source``
    and the source is left in place. Verification is NOT relaxed -- a
    failed read-back raises either way, because an unverified copy is not
    a backup either.

    ``retire_source=True`` (with the default ``remove_source=True``) is the
    safe-move mode: after a positive verification the source is moved aside
    into restorable staging (same filesystem, atomic rename) instead of
    being deleted, and a sweeper deletes it once ``retention_days``
    (default 7) expire. The manifest records ``staged_path``.

    Every move requires independent SHA256 manifests for source and remote
    destination in addition to rsync's checksum and the count/size read-back.
    The source must still match its pre-copy baseline immediately before it
    is retired. Unreadable, changed, or mismatching content refuses the move.
    ``verify_content_too`` controls optional copy-only verification; setting it
    False cannot disable the mandatory move gate. Copy-only callers use
    ``remove_source=False`` and leave the original untouched.

    Requires ``rsync`` ONLY when ``runner is None`` — i.e. only when this
    call will really shell out. An injected ``runner`` IS the transport, so
    demanding a binary it will never invoke would make the seam
    environment-dependent and fail honest callers for no reason.
    """
    # A move always uses an independent content baseline, even when an older
    # caller explicitly passes the former opt-in flag as False.
    baseline = (
        digest_tree(str(plan.source)) if remove_source or verify_content_too else None
    )
    if baseline is not None and (not baseline.usable or baseline.unreadable):
        refusal = ArchiveManifest(
            source=str(plan.source),
            destination=plan.destination,
            remote_path=plan.remote_path,
            size_bytes=plan.size_bytes,
            file_count=plan.file_count,
            checksummed=checksum,
            archived_at=time.time(),
            verified="could-not-look",
            verification_method="content",
            verification_evidence="Source cannot be fully hashed; left intact.",
        )
        atomic_json(plan.manifest_path, refusal.to_dict())
        raise ArchiveNotVerifiedError(refusal.verification_evidence)
    source_identity = plan.source.stat().st_dev, plan.source.stat().st_ino
    if runner is None:
        _rsync_binary()
    remote_parent = posixpath.dirname(plan.remote_path)
    if remote_parent and remote_parent not in _UNSAFE_REMOTE_PATHS:
        mkdir_result = exec_remote(
            plan.destination,
            f"mkdir -p -- {_quote_remote_path(remote_parent)}",
            runner=runner,
        )
        if not mkdir_result.success:
            raise RuntimeError(
                f"failed to create remote parent directory "
                f"{plan.destination}:{remote_parent} "
                f"(exit {mkdir_result.returncode}) -- source left untouched.\n"
                f"--- stdout ---\n{mkdir_result.stdout}\n"
                f"--- stderr ---\n{mkdir_result.stderr}"
            )

    # FREE-SPACE PREFLIGHT, before a single byte moves. Two-sided: we
    # already know the artifact size (plan.size_bytes), and this asks the
    # DESTINATION what it actually has. An estimate with no destination
    # probe passes on a full disk; a destination probe with no estimate
    # cannot say whether what is free is enough. Neither half is optional.
    #
    # "The NAS is roomy" is a capability claim, and a capability claim is
    # a measurement -- so it is asked, not assumed.
    available = remote_free_bytes(
        plan.destination,
        _quote_remote_path(remote_parent or plan.remote_path),
        runner=runner,
    )
    space = check_space(plan.size_bytes, available)
    if space.ok is False:
        raise InsufficientSpaceError(
            f"refusing to archive to {plan.destination}:{plan.remote_path} -- "
            f"{space.detail}\nSource left completely untouched. Free space on "
            f"the destination, or choose another one."
        )
    # space.ok is None means the probe could not answer. That is NOT
    # treated as a refusal: unlike sweep (which writes into the very
    # filesystem it is relieving, so an unmeasured destination is the
    # defect), archive writes to a REMOTE host and leaves the source
    # intact until the post-transfer read-back passes. A failed df on an
    # otherwise healthy destination should not block a migration that
    # will still be verified before anything is deleted. It is recorded
    # in the manifest so the gap is visible rather than silent.

    extra_opts: tuple[str, ...] = ("--checksum",) if checksum else ()
    result: SSHResult = sync_dir(
        plan.destination,
        _as_dir_contents(str(plan.source)),
        plan.remote_path,
        direction="push",
        exclude=exclude,
        extra_opts=extra_opts,
        runner=runner,
    )
    if not result.success:
        raise RuntimeError(
            f"archive sync to {plan.destination}:{plan.remote_path} failed "
            f"(exit {result.returncode}) -- source left untouched.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    # READ THE DESTINATION BACK before removing anything. rsync's exit code
    # says the transfer it attempted succeeded; it does not say the
    # destination now holds what the source held. Those are different
    # claims, and only the second one licenses an irreversible delete.
    #
    # The baseline is measured with `local_tally`, NOT `plan.file_count`:
    # the latter comes from the inode model, which deliberately excludes
    # symlinks-to-directories, while `rsync -a` writes them. Comparing
    # against it would fail a perfectly good archive -- the 2026-07-23
    # to_nas false alarm exactly.
    expected = local_tally(str(plan.source))
    probe = exec_remote(
        plan.destination,
        REMOTE_TALLY_CMD.format(path=_quote_remote_path(plan.remote_path)),
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
    verdict = verify_transfer(
        expected_count=expected.entry_count,
        expected_bytes=expected.size_bytes,
        observed=observed,
    )
    method = "tally"
    content_digest = ""

    # The second instrument is mandatory whenever the original will move or
    # be removed. Count-and-size remains a progress check, never a move gate.
    if (remove_source or verify_content_too) and verdict.may_remove_source:
        digest_probe = exec_remote(
            plan.destination,
            REMOTE_DIGEST_CMD.format(path=_quote_remote_path(plan.remote_path)),
            runner=runner,
        )
        source_manifest = baseline
        content_verdict = verify_content(
            source_manifest,
            parse_remote_manifest(
                digest_probe.stdout, probe_succeeded=digest_probe.success
            ),
        )
        # The STRICTER verdict wins, and it replaces rather than supplements:
        # a caller reading `verified` must see the answer from the gate that
        # actually decided, not from the weaker one that happened to run first.
        verdict = content_verdict
        method = "content"
        # The source was hashed anyway: keep its canonical digest as the
        # scrub baseline at zero marginal cost.
        content_digest = tree_digest(source_manifest)
        unchanged = verify_content(source_manifest, digest_tree(str(plan.source)))
        if not unchanged.may_remove_source:
            verdict = unchanged

    manifest = ArchiveManifest(
        source=str(plan.source),
        destination=plan.destination,
        remote_path=plan.remote_path,
        size_bytes=plan.size_bytes,
        file_count=plan.file_count,
        checksummed=checksum,
        archived_at=time.time(),
        verified=verdict.verdict,
        verification_evidence=verdict.evidence,
        # STATED, not implied. This call site is the only thing that knows
        # which check produced the verdict, so it is the only place that can
        # record it honestly. If a content-verified path is ever added here,
        # it sets "content" -- and a reviewer who forgets will leave "tally"
        # on a stronger check rather than the reverse, which is the safe
        # direction for a field that gates an irreversible delete.
        verification_method=method,
        sidecar_path=manifest_sidecar_remote_path(plan.remote_path),
        destination_tier=tier_of_destination(plan.destination),
        content_digest=content_digest,
        retained_source=not remove_source,
    )
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(plan.manifest_path, manifest.to_dict())

    if verdict.may_remove_source:
        # Manifest REPLICA next to the archived tree, so the map travels
        # with the data. Best-effort by contract (see write_manifest_sidecar):
        # a replica failure is recorded on the manifest and the local file
        # rewritten, but it never blocks a verified delete.
        sidecar = write_manifest_sidecar(
            plan.destination,
            plan.remote_path,
            plan.manifest_path,
            runner=runner,
        )
        manifest.sidecar_written = sidecar.written
        manifest.sidecar_detail = sidecar.detail
        atomic_json(plan.manifest_path, manifest.to_dict())

    if not verdict.may_remove_source:
        raise ArchiveNotVerifiedError(
            f"archive to {plan.destination}:{plan.remote_path} was NOT verified "
            f"({verdict.verdict}) -- SOURCE LEFT INTACT at {plan.source}.\n"
            f"{verdict.evidence}\n"
            f"The data is on the destination and the manifest is written, so "
            f"nothing is lost: re-run to retry, or inspect the destination and "
            f"remove the source by hand once you have accounted for the "
            f"difference. This verb will not delete an original it could not "
            f"confirm."
        )

    if remove_source:
        current = digest_tree(str(plan.source))
        identity = plan.source.stat().st_dev, plan.source.stat().st_ino
        if (
            identity != source_identity
            or not verify_content(baseline, current).may_remove_source
        ):
            manifest.verified = "mismatch"
            manifest.verification_evidence = (
                "Source changed during archive; source left intact."
            )
            atomic_json(plan.manifest_path, manifest.to_dict())
            raise ArchiveNotVerifiedError(manifest.verification_evidence)
        if retire_source:
            from ._retention import stage_for_retention

            staged = stage_for_retention(plan.source, retention_days=retention_days)
            manifest.retained_source = True
            manifest.staged_path = staged.staged_path
            atomic_json(plan.manifest_path, manifest.to_dict())
        else:
            shutil.rmtree(plan.source)
    return manifest


# EOF
