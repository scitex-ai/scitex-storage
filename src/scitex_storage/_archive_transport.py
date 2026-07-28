#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_archive_transport.py
"""Transport plumbing shared by ``archive`` (push) and ``restore`` (pull).

Extracted when ``_archive.py`` outgrew the line limit. The split is along
the seam that was already there: these helpers are about SPEAKING TO A
REMOTE HOST -- quoting a path for a remote shell, locating rsync, naming
the manifest -- and are direction-agnostic. Everything about *whether it
is safe to delete an original* stayed in ``_archive.py``, because only
that direction deletes anything.

Nothing here is new; it is the same code, in the place its callers share.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from ._scan import MissingSystemDependencyError

DESTINATIONS: tuple[str, ...] = ("nas", "nas2")
DEFAULT_REMOTE_ROOT = "~/scitex-storage-archive"

#: Remote paths this dangerous are never a legitimate archive/restore
#: target -- a real path always has more structure than this after
#: flattening a real local absolute path under DEFAULT_REMOTE_ROOT.
_UNSAFE_REMOTE_PATHS = {"", "/", "~", "."}

_RSYNC_INSTALL_HINT = """scitex-storage `archive`/`restore` require the `rsync` binary.

Both verbs move data via scitex-ssh's `sync_dir`, which is a wrapper over
`rsync -a` over ssh -- so the LOCAL rsync binary is a hard runtime
dependency of the transport, exactly as `fd` is of `scan`. It was not
found on PATH.

Install it:
  Debian/Ubuntu:  sudo apt install rsync
  macOS:          brew install rsync   (or use the preinstalled /usr/bin/rsync)
  other/manual:   https://rsync.samba.org/

Note the REMOTE host needs rsync too -- but a missing remote binary fails
differently (an ssh-side error naming rsync), so this message is only ever
about the local side."""


def _rsync_binary() -> str:
    """Return the path to the local ``rsync``, or raise fail-loud.

    Mirrors ``_scan.py``'s ``_fd_binary()``: raised at CALL time (never at
    import time), never caught inside this package, and never degraded into
    a silent fallback -- there is no meaningful fallback for a transport.

    ``sync_dir`` would otherwise surface a bare ``FileNotFoundError`` from
    inside a *sibling package* (scitex-ssh), naming a binary this package's
    own docs never mentioned, leaving the caller to reverse-engineer
    scitex-storage -> scitex-ssh -> rsync. Declared in ``_system_deps.py``.
    """
    found = shutil.which("rsync")
    if found:
        return found
    raise MissingSystemDependencyError(_RSYNC_INSTALL_HINT)


def _quote_remote_path(path: str) -> str:
    """shlex.quote ``path`` for a remote shell without breaking a leading
    ``~`` home-dir shortcut.

    Shell tilde-expansion only applies to an UNQUOTED (or quote-adjacent)
    leading ``~`` -- naively wrapping the whole string in single quotes (a
    bare ``shlex.quote(path)``) turns ``~`` into a literal character, so
    ``mkdir -p '~/scitex-storage-archive/x'`` creates a bogus directory
    literally named ``~`` instead of expanding to ``$HOME``. Confirmed via
    scitex-ssh smoke-testing a real ``mkdir`` on nas2 (2026-07-11) -- the
    stray ``~`` dir was found sitting in the remote home directory.
    """
    if path == "~":
        return "~"
    if path.startswith("~/"):
        return "~/" + shlex.quote(path[2:])
    return shlex.quote(path)


def _as_dir_contents(path: str) -> str:
    """Append a trailing ``/`` (idempotent) so rsync copies ``path``'s
    CONTENTS into the destination rather than nesting ``path`` itself one
    level deeper as a subdirectory."""
    return path.rstrip("/") + "/"


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

# EOF
