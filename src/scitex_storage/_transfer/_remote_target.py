#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_remote_target.py
"""A bounded, independent re-hash of a REMOTE archive copy for retention sweeps.

``sweep_restorable`` deletes an expired staged original only when its copy
still verifies TODAY. For a local move it re-hashes the destination itself;
for a remote archive it needs a caller-supplied ``target_verifier`` and,
without one, defers forever. That left every verified archive (and every
``tier`` run) holding its original on the hot volume indefinitely.

:func:`remote_target_verifier` is that verifier. It re-hashes the remote
tree with the same BusyBox-safe digest command the archive gate used and
answers True only when the canonical digest reproduces the manifest's
baseline, every entry was read, and the work finished before the sweep's
deadline (the ssh call itself is given the remaining time as its timeout).
Anything else (unreachable, timeout, unreadable entries, a different digest,
a receipt that does not load) is False, and the original is kept.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from scitex_ssh import exec_remote

from ._archive import ArchiveManifest, remote_tree_digest
from ._archive_transport import _quote_remote_path
from ._content_verify import tree_digest
from ._remote_digest import REMOTE_DIGEST_CMD, parse_remote_manifest


def remote_target_verifier(*, runner=None):
    """Build a ``target_verifier`` for :func:`sweep_restorable`.

    ``runner`` is the scitex-ssh seam, so tests drive a real local shell
    instead of an ssh connection.
    """

    def verify(target, *, deadline, cancelled, max_bytes, max_entries) -> bool:
        if not isinstance(target, dict) or target.get("kind") != "remote":
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancelled():
            return False
        try:
            data = json.loads(Path(target["manifest_path"]).read_text(encoding="utf-8"))
            manifest = ArchiveManifest.from_dict(data)
        except (OSError, KeyError, TypeError, ValueError):
            return False
        expected = remote_tree_digest(manifest)
        if not expected or manifest.remote_path != target.get("path"):
            return False
        try:
            probe = exec_remote(
                str(target.get("host", "")),
                REMOTE_DIGEST_CMD.format(path=_quote_remote_path(manifest.remote_path)),
                timeout=remaining,
                runner=runner,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        remote = parse_remote_manifest(probe.stdout, probe_succeeded=probe.success)
        if not remote.usable or remote.unreadable or len(remote.digests) > max_entries:
            return False
        return (
            tree_digest(remote) == expected
            and time.monotonic() < deadline
            and not cancelled()
        )

    return verify


# EOF
