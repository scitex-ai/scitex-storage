#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_restore.py
"""Pull an archived directory back to its original local path.

Split out of ``_archive.py``, which was named for one verb while
implementing two OPPOSITE data flows: archive pushes local -> remote and
then DELETES the local original, while restore pulls remote -> local and
deliberately destroys nothing.

They diverge in exactly the way that matters: every safety mechanism the
archive direction has accumulated -- the destination read-back, the
free-space preflight, ``ArchiveNotVerifiedError`` -- exists because
archive removes an original. Restore removes nothing by default, so none
of it applies. Keeping them in one module meant the destructive verb's
machinery kept growing around a non-destructive one that never needed it.

RESTORE NEVER DESTROYS THE ARCHIVE BY DEFAULT. ``delete_remote`` is
opt-in, because restoring a copy locally is not a statement that the
backup is now redundant -- and if the restore itself was prompted by
suspicion about the local copy, deleting the remote is precisely the
wrong reflex.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from scitex_ssh import SSHResult, exec_remote, sync_dir

from ._archive_transport import (
    _UNSAFE_REMOTE_PATHS,
    _as_dir_contents,
    _manifest_path,
    _quote_remote_path,
    _rsync_binary,
)
from ._audit import logged_transfer, transfer_stage


class RestoreNotVerifiedError(RuntimeError):
    """The restored data does not reproduce the manifest's content digest.

    Raised only when the caller asked for ``verify_content``. The remote copy
    is untouched, whatever ``delete_remote`` said.
    """


def restored_digest(path: Path, *, is_file: bool) -> str:
    """Content digest of restored data, in the manifest's convention.

    A file is its own sha256 and a directory is the canonical tree digest,
    matching ``ArchiveManifest.content_digest``. Empty when the data cannot
    be completely read, so it can never equal a real baseline.
    """
    from ._content_verify import digest_file_unit, digest_tree, tree_digest

    if is_file:
        manifest = digest_file_unit(str(path))
        return "" if manifest.root_missing else manifest.digests.get(path.name, "")
    manifest = digest_tree(str(path))
    if not manifest.usable or manifest.unreadable or not manifest.digests:
        return ""
    return tree_digest(manifest)


@dataclass
class RestorePlan:
    """The result of :func:`plan_restore` — never touches the network."""

    manifest: object  # ArchiveManifest; untyped here to avoid a cycle
    manifest_path: Path


def plan_restore(source: str | Path) -> RestorePlan:
    """Load the manifest for ``source`` — read-only, never touches the network.

    ``source`` need not currently exist (it typically doesn't — archiving
    removed it). Fail-loud if no manifest was ever written for this path.
    """
    from ._archive import ArchiveManifest

    resolved = Path(source).expanduser().resolve()
    manifest_path = _manifest_path(resolved)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no archive manifest found for {resolved} at {manifest_path} "
            "-- was this directory ever archived from here?"
        )
    manifest = ArchiveManifest.from_dict(json.loads(manifest_path.read_text()))
    return RestorePlan(manifest=manifest, manifest_path=manifest_path)


@logged_transfer("restore")
def apply_restore(
    plan: RestorePlan,
    *,
    delete_remote: bool = False,
    verify_content: bool = False,
    runner=None,
) -> Path:
    """Pull the archived directory (or single file) back to its original path.

    Verifies via rsync's own exit code; raises loud on failure (nothing is
    removed remotely in that case, regardless of ``delete_remote``). The
    remote copy is only removed when ``delete_remote=True`` — off by
    default, since restoring locally should not destroy the backup unless
    explicitly asked. ``runner`` is passed straight through to both
    ``sync_dir`` and ``exec_remote``.

    ``verify_content=True`` re-hashes the restored data and requires it to
    reproduce the manifest's ``content_digest`` before anything else happens,
    ``delete_remote`` included. A missing baseline or a different digest
    raises :class:`RestoreNotVerifiedError`. It is off by default so existing
    callers that restore into a non-empty directory keep merge semantics.

    A manifest whose ``source_kind`` is ``"file"`` restores the single entry
    ``remote_path/<name>`` to the original file path.

    Requires ``rsync`` only when ``runner is None`` — same reasoning as
    :func:`~scitex_storage._transfer._archive.apply_archive`.
    """
    if runner is None:
        _rsync_binary()
    manifest = plan.manifest
    source = Path(manifest.source)
    is_file = getattr(manifest, "source_kind", "directory") == "file"
    remote = (
        f"{manifest.remote_path.rstrip('/')}/{source.name}"
        if is_file
        else _as_dir_contents(manifest.remote_path)
    )
    transfer_stage("rsync")
    result: SSHResult = sync_dir(
        manifest.destination,
        str(source),
        remote,
        direction="pull",
        runner=runner,
    )
    if not result.success:
        transfer_stage("rsync", exit_code=result.returncode)
        raise RuntimeError(
            f"restore pull from {manifest.destination}:{manifest.remote_path} "
            f"failed (exit {result.returncode}) -- remote copy untouched.\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )

    if verify_content:
        transfer_stage("verification")
        if not manifest.content_digest:
            raise RestoreNotVerifiedError(
                f"{source} was restored, but its manifest records no content "
                f"digest to verify against -- the remote copy was left untouched."
            )
        if restored_digest(source, is_file=is_file) != manifest.content_digest:
            raise RestoreNotVerifiedError(
                f"restored {source} does not reproduce the manifest content "
                f"digest -- the remote copy at {manifest.destination}:"
                f"{manifest.remote_path} was left untouched."
            )

    if delete_remote:
        transfer_stage("remote_cleanup")
        if manifest.remote_path.strip() in _UNSAFE_REMOTE_PATHS:
            raise ValueError(
                f"refusing to delete an unsafe remote path: {manifest.remote_path!r}"
            )
        rm_result = exec_remote(
            manifest.destination,
            f"rm -rf -- {_quote_remote_path(manifest.remote_path)}",
            runner=runner,
        )
        if not rm_result.success:
            raise RuntimeError(
                f"local restore succeeded, but removing the remote copy at "
                f"{manifest.destination}:{manifest.remote_path} failed "
                f"(exit {rm_result.returncode}) -- remote copy still present.\n"
                f"--- stdout ---\n{rm_result.stdout}\n"
                f"--- stderr ---\n{rm_result.stderr}"
            )

    return source


# EOF
