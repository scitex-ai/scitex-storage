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
import shlex
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from scitex_ssh import SSHResult, exec_remote, sync_dir

from ._scan import _measure_dir

DESTINATIONS: tuple[str, ...] = ("nas", "nas2")
DEFAULT_REMOTE_ROOT = "~/scitex-storage-archive"

# Remote paths this dangerous are never a legitimate archive/restore target
# -- a real path always has more structure than this after flattening a
# real local absolute path under DEFAULT_REMOTE_ROOT.
_UNSAFE_REMOTE_PATHS = {"", "/", "~", "."}


def _manifest_dir() -> Path:
    """Resolved fresh on every call (not a module-level constant) so tests
    can sandbox it by setting ``$HOME`` before calling in — matching the
    pattern already used for ``scan``'s default roots."""
    return Path("~/.scitex/scitex-storage/runtime/archive-manifests").expanduser()


def _default_remote_path(source: Path, remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    """Mirror ``source``'s absolute path under ``remote_root``, flattened."""
    flattened = str(source).lstrip("/")
    return f"{remote_root.rstrip('/')}/{flattened}"


def _manifest_path(source: Path) -> Path:
    """Deterministic manifest filename for ``source`` (so restore can find it)."""
    flattened = str(source).lstrip("/").replace("/", "--")
    return _manifest_dir() / f"{flattened}.json"


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
    runner=None,
) -> ArchiveManifest:
    """Execute an archive plan: push, verify, write manifest, THEN remove source.

    ``checksum`` (default on, since this precedes an irreversible local
    delete) adds rsync's ``--checksum`` flag — reads every byte on both
    sides instead of rsync's fast mtime+size quick-check. A non-zero rsync
    exit raises immediately; the source is left completely untouched and no
    manifest is written. ``runner`` is passed straight through to
    ``sync_dir`` (a ``subprocess.run``-shaped invoker) — the same
    real-transport-free test seam scitex-ssh itself exposes.
    """
    extra_opts: tuple[str, ...] = ("--checksum",) if checksum else ()
    result: SSHResult = sync_dir(
        plan.destination,
        str(plan.source),
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

    manifest = ArchiveManifest(
        source=str(plan.source),
        destination=plan.destination,
        remote_path=plan.remote_path,
        size_bytes=plan.size_bytes,
        file_count=plan.file_count,
        checksummed=checksum,
        archived_at=time.time(),
    )
    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

    shutil.rmtree(plan.source)
    return manifest


@dataclass
class RestorePlan:
    """The result of :func:`plan_restore` — never touches the network."""

    manifest: ArchiveManifest
    manifest_path: Path


def plan_restore(source: str | Path) -> RestorePlan:
    """Load the manifest for ``source`` — read-only, never touches the network.

    ``source`` need not currently exist (it typically doesn't — archiving
    removed it). Fail-loud if no manifest was ever written for this path.
    """
    resolved = Path(source).expanduser().resolve()
    manifest_path = _manifest_path(resolved)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no archive manifest found for {resolved} at {manifest_path} "
            "-- was this directory ever archived from here?"
        )
    manifest = ArchiveManifest.from_dict(json.loads(manifest_path.read_text()))
    return RestorePlan(manifest=manifest, manifest_path=manifest_path)


def apply_restore(
    plan: RestorePlan, *, delete_remote: bool = False, runner=None
) -> Path:
    """Pull the archived directory back to its original local path.

    Verifies via rsync's own exit code; raises loud on failure (nothing is
    removed remotely in that case, regardless of ``delete_remote``). The
    remote copy is only removed when ``delete_remote=True`` — off by
    default, since restoring locally should not destroy the backup unless
    explicitly asked. ``runner`` is passed straight through to both
    ``sync_dir`` and ``exec_remote``.
    """
    manifest = plan.manifest
    source = Path(manifest.source)
    result: SSHResult = sync_dir(
        manifest.destination,
        str(source),
        manifest.remote_path,
        direction="pull",
        runner=runner,
    )
    if not result.success:
        raise RuntimeError(
            f"restore pull from {manifest.destination}:{manifest.remote_path} "
            f"failed (exit {result.returncode}) -- remote copy untouched.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    if delete_remote:
        if manifest.remote_path.strip() in _UNSAFE_REMOTE_PATHS:
            raise ValueError(
                f"refusing to delete an unsafe remote path: {manifest.remote_path!r}"
            )
        rm_result = exec_remote(
            manifest.destination,
            f"rm -rf -- {shlex.quote(manifest.remote_path)}",
            runner=runner,
        )
        if not rm_result.success:
            raise RuntimeError(
                f"local restore succeeded, but removing the remote copy at "
                f"{manifest.destination}:{manifest.remote_path} failed "
                f"(exit {rm_result.returncode}) -- remote copy still present.\n"
                f"--- stdout ---\n{rm_result.stdout}\n--- stderr ---\n{rm_result.stderr}"
            )

    return source


# EOF
