#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact duplicate-file detection — a separate, explicitly opt-in verb.

``scan`` (see ``_scan.py``) is deliberately **stat-only**: it never reads
file *contents*, precisely so it is always safe to point at a nearly-full
disk or a slow network mount (a byte-reading "du-storm" is exactly the
failure mode ``scan`` was reworked to avoid — see the PR that introduced
the per-child size+inode design). Finding *exact* duplicates fundamentally
requires reading (hashing) file contents, so that capability cannot live
inside ``scan`` without breaking its own safety contract. It is instead its
own verb, ``find-duplicates`` / :func:`find_duplicates`, that an operator
must explicitly choose to run.

PERFORMANCE: even so, a hand-rolled Python ``hashlib`` size+hash pass is
the wrong tool at multi-terabyte scale — the same rationale as ``scan``'s
``fd`` delegation. This module shells out to ``fclones``
(https://github.com/pkolaczk/fclones), an established, actively-maintained
Rust duplicate-file finder that already implements a highly efficient
group-by-size, then parallel-hash-prefix, then parallel-hash-suffix, then
full-content-hash pipeline (minimizing bytes actually read compared to a
naive "hash every candidate fully" approach) instead of a hand-rolled
reimplementation. ``fclones`` is a **system** (non-PyPI) runtime dependency
of this verb only (see ``_system_deps.py`` and the README) — never required
to *install* scitex-storage. A missing binary raises
:class:`~scitex_storage._scan.MissingSystemDependencyError` with install
instructions rather than silently falling back to a slow pure-Python hash.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ._scan import MissingSystemDependencyError

_FCLONES_BINARY_NAME = "fclones"

_FCLONES_INSTALL_HINT = """scitex-storage `find-duplicates` requires the `fclones` binary — a \
hand-rolled Python size+hash pass is too slow at multi-terabyte scale.

`fclones` was not found on PATH. Install it:
  cargo:          cargo install fclones
  brew:           brew install fclones
  other/manual:   https://github.com/pkolaczk/fclones/releases

See https://github.com/pkolaczk/fclones for details."""


def _fclones_binary() -> str:
    """Return the path to ``fclones``.

    Raises :class:`MissingSystemDependencyError` (never falls back to a
    Python hash pass) if it is not on ``PATH``.
    """
    found = shutil.which(_FCLONES_BINARY_NAME)
    if found:
        return found
    raise MissingSystemDependencyError(_FCLONES_INSTALL_HINT)


def find_duplicates(
    roots: list[str | Path], max_depth: int | None = None
) -> list[list[Path]]:
    """Find groups of files with byte-identical content under ``roots``.

    Unlike :func:`scitex_storage.scan`, this READS FILE CONTENTS (via
    ``fclones``'s parallel prefix/suffix/full-content hashing) — there is
    no stat-only equivalent by definition; exact duplicate detection
    requires reading bytes. Use ``max_depth`` to bound the walk on a slow
    network mount or a login node.

    Read-only in the sense that nothing is moved, linked, or deleted —
    ``fclones group`` (not ``fclones link``/``remove``/``dedupe``) only
    ever reports.

    Raises ``FileNotFoundError`` / ``NotADirectoryError`` for a bad root
    (fail-loud, matching :func:`scitex_storage.scan`) and
    :class:`MissingSystemDependencyError` if ``fclones`` is not installed.
    """
    if not roots:
        return []

    resolved: list[Path] = []
    for raw_root in roots:
        p = Path(raw_root).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"path does not exist: {p}")
        if not p.is_dir():
            raise NotADirectoryError(f"not a directory: {p}")
        resolved.append(p.resolve())

    fclones_bin = _fclones_binary()
    cmd = [fclones_bin, "group", "--hidden", "--no-ignore", "--format", "json"]
    if max_depth is not None:
        cmd += ["--depth", str(max_depth)]
    cmd += [str(p) for p in resolved]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"`fclones group` exited {proc.returncode}: {stderr or '(no stderr output)'}"
        )

    payload = json.loads(proc.stdout.decode("utf-8"))
    groups: list[list[Path]] = []
    for group in payload.get("groups", []):
        paths = sorted(Path(p) for p in group.get("files", []))
        if len(paths) >= 2:
            groups.append(paths)
    groups.sort(key=lambda g: len(g), reverse=True)
    return groups


# EOF
