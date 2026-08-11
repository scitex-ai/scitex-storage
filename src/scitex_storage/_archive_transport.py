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

#: The LIVE storage hosts. Renamed 2026-08-07; this package kept pointing at
#: the old names until 2026-08-11, which meant `archive --to` accepted ONLY
#: destinations that refuse the connection. Verified by probing each alias and
#: reading its real exit code (not through a pipe, which returns the pipe's):
#:     scitex-nas-01  rc=0  ALIVE:WATANAS1
#:     scitex-nas-02  rc=0  ALIVE:WATANAS2
#:     scitex-nas-03  rc=0  ALIVE:DXP480TPLUS-994
DESTINATIONS: tuple[str, ...] = (
    "scitex-nas-01",
    "scitex-nas-02",
    "scitex-nas-03",
)

#: RETIRED aliases, still ACCEPTED and rewritten to their replacement.
#:
#: `--to` is a published CLI contract, so this is a MIGRATION, not a rename:
#: someone's script, cron entry or muscle memory says `--to nas2`, and breaking
#: it outright would turn a working invocation into a usage error at exactly
#: the moment they are trying to move data. Accept, rewrite, and SAY SO.
#:
#: The mapping is measured, not guessed -- ssh itself refuses each retired
#: alias with a message naming its replacement. Note nas -> scitex-nas-03: the
#: plausible-looking nas -> scitex-nas-00 points at a hostname that does not
#: resolve, which is why this table is transcribed from the hosts rather than
#: inferred from the numbering.
RETIRED_DESTINATIONS: dict[str, str] = {
    "nas": "scitex-nas-03",
    "nas1": "scitex-nas-01",
    "nas2": "scitex-nas-02",
}


def resolve_destination(destination: str) -> tuple[str, str | None]:
    """Map a possibly-retired destination alias onto a live one.

    Returns ``(live_alias, notice)``. ``notice`` is ``None`` for a name that
    was already current, and a caller-facing sentence when a retired alias was
    rewritten -- returned rather than logged so the CLI can print it and the
    library can ignore it, instead of this module guessing which it is.

    An unknown name is returned UNCHANGED with no notice: validating the
    destination is ``plan_archive``'s job and it already raises with the legal
    set. Silently correcting a typo here would let a wrong host through under a
    name the caller never typed, which for a verb that deletes the source is
    the worst possible place to be helpful.
    """
    live = RETIRED_DESTINATIONS.get(destination)
    if live is None:
        return destination, None
    return live, (
        f"'{destination}' was retired on 2026-08-07; using '{live}' instead. "
        f"Update your command to '--to {live}' -- this rewrite will be removed."
    )


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
