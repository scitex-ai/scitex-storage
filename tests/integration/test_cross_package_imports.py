#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-package integration gate (PS-140) — runtime import contract.

scitex-storage imports three cross-package modules under ``src/``:

* ``scitex_dev.ecosystem`` (``_cli/_compat.py``) — the CliHelp /
  SpecCommand / SpecGroup help-spec helpers. OPTIONAL/guarded (a lean
  install with no ``[dev]`` extra still works via a fallback).
* ``scitex_dev._cli._completion`` (``_cli/__init__.py``) —
  ``attach_shell_completion``, wiring ``install-shell-completion`` /
  ``print-shell-completion``. OPTIONAL/guarded, same reason.
* ``scitex_ssh`` (``_archive.py``) — ``sync_dir`` / ``exec_remote`` /
  ``SSHResult``, the transport ``archive``/``restore`` are built on.
  REQUIRED (a hard ``[project.dependencies]`` entry, unguarded) — archive
  tiering has no meaning without a transport, unlike the two optional
  scitex-dev help/completion niceties above.

This gate proves that when a listed package IS installed, the import
actually resolves — catching a renamed/moved upstream API before it ships.

``CROSS_PACKAGE_IMPORTS`` is the audited source of truth: it must list
exactly the cross-package modules imported under ``src/`` (audit-project
verifies it). Keep it in sync with the imports in ``_cli/_compat.py``,
``_cli/__init__.py``, and ``_archive.py``.
"""

from __future__ import annotations

import importlib

import pytest

# Exactly the cross-package imports found under src/ (PS-140 verifies this set).
CROSS_PACKAGE_IMPORTS = [
    "scitex_dev.ecosystem",
    "scitex_dev._cli._completion",
    "scitex_ssh",
]


@pytest.mark.parametrize("module_name", CROSS_PACKAGE_IMPORTS)
def test_cross_package_dependency_imports_cleanly(module_name):
    # Arrange — skip when the optional sibling isn't installed (lean install).
    pytest.importorskip(module_name)
    # Act
    module = importlib.import_module(module_name)
    # Assert
    assert module is not None


def test_compat_uses_the_real_clihelp_when_scitex_dev_is_installed():
    # Arrange — only meaningful once scitex-dev is on the path.
    pytest.importorskip("scitex_dev.ecosystem")
    from scitex_storage._cli import _compat

    # Act
    has_spec_help = _compat.HAS_SPEC_HELP
    # Assert
    assert has_spec_help is True


def test_cli_wires_shell_completion_when_scitex_dev_is_installed():
    # Arrange — only meaningful once scitex-dev is on the path.
    pytest.importorskip("scitex_dev._cli._completion")
    from scitex_storage._cli import main

    # Act
    has_completion_cmd = "print-shell-completion" in main.commands
    # Assert
    assert has_completion_cmd is True


def test_archive_module_resolves_sync_dir_from_scitex_ssh():
    # Arrange -- a hard dependency, so this should always resolve.
    from scitex_storage import _archive

    # Act
    # Assert
    assert callable(_archive.sync_dir)


# EOF
