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
import shlex
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scitex_ssh import SSHResult, exec_remote, sync_dir

from ._content_verify import digest_tree, verify_content
from ._remote_digest import REMOTE_DIGEST_CMD, parse_remote_manifest

from ._archive_transport import (
    DEFAULT_REMOTE_ROOT,
    DESTINATIONS,
    _UNSAFE_REMOTE_PATHS,
    _as_dir_contents,
    _default_remote_path,
    _manifest_dir,
    _manifest_path,
    _quote_remote_path,
    _rsync_binary,
)
from ._restore import RestorePlan, apply_restore, plan_restore
from ._scan import MissingSystemDependencyError, _measure_dir
from ._space import remote_free_bytes
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

    Read-only: stats ``source`` via the same walk :func:`~scitex_storage._scan.scan`
    uses, never touches the network. Fail-loud on a missing / non-directory
    ``source`` or an unrecognised ``destination``.

    Deliberately does NOT require ``rsync``: planning is genuinely
    transport-free, and the ``runner`` seam on :func:`apply_archive` means a
    caller may legitimately plan here and transport by other means. The
    binary is required by the code that actually invokes it (see
    :func:`_rsync_binary`'s call sites) and, for the CLI's benefit, checked
    up-front in ``_cli/_archive_cmd.py`` so the DEFAULT dry-run cannot
    promise a run that could not start.
    """
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
    #: HOW the destination was checked, as DATA rather than as prose buried in
    #: the evidence string.
    #:
    #: WHY THIS FIELD EXISTS. `verified: "verified"` says a check passed and
    #: says NOTHING about what the check could see. This one is a TALLY:
    #: entry count plus byte total. It is the right cheap check to watch a
    #: transfer with, and `_content_verify`'s discriminating test asserts in
    #: CI that it returns may_remove_source=True for a destination file with
    #: the right name, the right LENGTH and the WRONG BYTES. A reader of this
    #: manifest was given no way to know that, and `apply_archive` deletes the
    #: source on this verdict -- so the manifest recorded a stronger claim
    #: than the code had earned.
    #:
    #: The defect is prose-versus-data, which this project has now hit three
    #: times: a docstring that asserted verification the body did not perform
    #: (fixed 2026-07-28), a module docstring naming a dependency the data
    #: correctly excluded (a grep "confirmed" the bug was still present), and
    #: this. THE FIX IS ALWAYS THE SAME -- put the claim in a field a consumer
    #: reads, not in a sentence a human might.
    #:
    #: "tally"   entry count + byte total. Cannot see same-length corruption.
    #: "content" sha256 per entry. Not yet reachable here: the destination is
    #:           remote and `digest_tree` hashes a local path. Tracked on
    #:           storage-archive-deletes-on-a-tally-gated-check-20260811.
    #:
    #: Defaulted to "unknown" rather than "tally" ON PURPOSE. Manifests written
    #: before this field existed were ALSO tally-verified, but that is my
    #: inference about old artefacts rather than something they recorded, and
    #: back-filling it would manufacture evidence. An old manifest genuinely
    #: does not say, and "unknown" is what it does not say.
    verification_method: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ArchiveManifest:
        return cls(**data)


def apply_archive(
    plan: ArchivePlan,
    *,
    checksum: bool = True,
    exclude: tuple[str, ...] = (),
    verify_content_too: bool = False,
    runner=None,
) -> ArchiveManifest:
    """Execute an archive plan: push, READ THE DESTINATION BACK, write the
    manifest, and remove the source ONLY on a positive verification.

    Until 2026-07-28 this docstring already said "verify" and the body did
    not do it — the source was removed on rsync's exit code alone. A
    docstring asserting a safety step the code does not perform is its own
    defect, because the next reader budgets trust against it; that is how
    the gap survived. The verification is now real:

    * the destination is tallied independently (entry count + total bytes)
      and compared against the source measured the SAME way;
    * a mismatch OR an unanswerable probe raises
      :class:`ArchiveNotVerifiedError` and leaves the source intact — "I
      could not check" blocks the delete exactly as firmly as "the check
      failed", because for a destructive action they have the same
      consequence;
    * the verdict and its evidence are written into the manifest either
      way, so a refusal is auditable rather than living only in whoever's
      terminal saw the exception.

    ``checksum`` (default on, since this precedes an irreversible local
    delete) adds rsync's ``--checksum`` flag — reads every byte on both
    sides instead of rsync's fast mtime+size quick-check. That verifies the
    bytes rsync transferred; the read-back answers the different question of
    whether the destination now holds what the source held. A non-zero rsync
    exit raises immediately; the source is left completely untouched and no
    manifest is written. ``runner`` is passed straight through to both
    ``exec_remote`` (the mkdir) and ``sync_dir`` (a ``subprocess.run``-
    shaped invoker) — the same real-transport-free test seam scitex-ssh
    itself exposes.

    Requires ``rsync`` ONLY when ``runner is None`` — i.e. only when this
    call will really shell out. An injected ``runner`` IS the transport, so
    demanding a binary it will never invoke would make the seam
    environment-dependent and fail honest callers for no reason.
    """
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
        plan.destination, _quote_remote_path(remote_parent or plan.remote_path),
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
        expected_count=expected.entry_count or 0,
        expected_bytes=expected.size_bytes or 0,
        observed=observed,
    )
    method = "tally"

    # OPTIONAL SECOND GATE: compare CONTENT, not a tally of it.
    #
    # Only reached when the tally already passed. Running it after a MISMATCH
    # would spend hours hashing a destination we already know is wrong, and
    # running it after a COULD_NOT_LOOK would be hashing something we could
    # not even count.
    #
    # WHY THIS IS OPT-IN AND NOT THE DEFAULT, stated because "the strongest
    # gate before an irreversible delete" is the obvious-sounding argument for
    # the opposite and I made it before measuring:
    #
    # `checksum=True` (the default) already passes rsync `--checksum`, which
    # reads every byte on BOTH SIDES. Transfer-time corruption is therefore
    # already covered. Hashing both trees again would roughly DOUBLE the cost
    # of a multi-terabyte archive to close a NARROWER residual window:
    #
    #   * bit rot at the destination AFTER rsync finished, before the delete;
    #   * a destination mutated by something other than this transfer;
    #   * a destination left half-populated by an earlier failed run whose
    #     files happen to agree on count and size;
    #   * any call made with checksum=False, where the in-flight class returns.
    #
    # A default nobody can afford to leave on gets turned off, and then the
    # honest `tally` stamp becomes the normal case anyway. What makes opt-in
    # acceptable rather than a hiding place is that the manifest records which
    # gate actually ran -- see ArchiveManifest.verification_method.
    if verify_content_too and verdict.may_remove_source:
        digest_probe = exec_remote(
            plan.destination,
            REMOTE_DIGEST_CMD.format(path=_quote_remote_path(plan.remote_path)),
            runner=runner,
        )
        content_verdict = verify_content(
            digest_tree(str(plan.source)),
            parse_remote_manifest(
                digest_probe.stdout, probe_succeeded=digest_probe.success
            ),
        )
        # The STRICTER verdict wins, and it replaces rather than supplements:
        # a caller reading `verified` must see the answer from the gate that
        # actually decided, not from the weaker one that happened to run first.
        verdict = content_verdict
        method = "content"

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
    )
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

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

    shutil.rmtree(plan.source)
    return manifest




# EOF
