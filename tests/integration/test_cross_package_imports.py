#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-package integration gate (PS-140) — runtime import contract.

scitex-storage's CLI/package imports several cross-package modules under
``src/``, all guarded (except ``scitex_ssh``) so a lean install (no
``[dev]`` extra) still works:

* ``scitex_dev.ecosystem`` (``_cli/_compat.py``) — the CliHelp /
  SpecCommand / SpecGroup help-spec helpers. OPTIONAL/guarded (a lean
  install with no ``[dev]`` extra still works via a fallback).
* ``scitex_dev._cli._completion`` (``_cli/__init__.py``) —
  ``attach_shell_completion``, wiring ``install-shell-completion`` /
  ``print-shell-completion``. OPTIONAL/guarded, same reason.
* ``scitex_dev.system_deps`` (``_system_deps.py``) — ``SystemDepSpec``,
  used by the ``scitex_dev.system_deps`` entry-point provider that
  declares scitex-storage's ``fd``/``fclones`` system dependencies to the
  fleet-wide aggregator (``scitex-dev ecosystem system-deps``).
  OPTIONAL/guarded, same reason.
* ``scitex_ssh`` (``_archive.py``) — ``sync_dir`` / ``exec_remote`` /
  ``SSHResult``, the transport ``archive``/``restore`` are built on.
  REQUIRED (a hard ``[project.dependencies]`` entry, unguarded) — archive
  tiering has no meaning without a transport, unlike the optional
  scitex-dev help/completion/system-deps niceties above.
* ``scitex_app._django`` / ``scitex_app._standalone``
  (``_django/_app_adapter.py``) — the (currently private) Django
  AppConfig base class + standalone-server launcher every scitex-hub
  plugin needs. OPTIONAL/guarded via the ``gui`` extra (see
  ``pyproject.toml``) — imported ONLY from the isolated adapter module,
  never elsewhere (see that module's docstring for why).
* ``scitex_ui`` (``_django/settings.py`` / ``_django/_server.py``) —
  supplies the shared workspace shell template + CSS/JS the GUI's
  templates extend. OPTIONAL/guarded, same ``gui`` extra.

This gate proves that when a listed package IS installed, the import
actually resolves — catching a renamed/moved upstream API before it ships.

``CROSS_PACKAGE_IMPORTS`` is the audited source of truth: it must list
exactly the cross-package modules imported under ``src/`` (audit-project
verifies it). Keep it in sync with the imports in ``_cli/_compat.py``,
``_cli/__init__.py``, ``_system_deps.py``, ``_archive.py``, and
``_django/_app_adapter.py``.
"""

from __future__ import annotations

import importlib

import pytest

# Exactly the cross-package imports found under src/ (PS-140 verifies this set).
CROSS_PACKAGE_IMPORTS = [
    "scitex_dev.ecosystem",
    "scitex_dev._cli._completion",
    "scitex_dev.system_deps",
    "scitex_ssh",
    "scitex_app._django",
    "scitex_app._standalone",
    "scitex_ui",
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


def test_app_adapter_resolves_the_real_scitex_app_config_when_installed():
    # Arrange — only meaningful once scitex-app + Django are on the path.
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    from scitex_app._django import ScitexAppConfig as RealScitexAppConfig

    from scitex_storage._django._app_adapter import ScitexAppConfig

    # Act
    resolved = ScitexAppConfig
    # Assert — the adapter must re-export the REAL base class (not the
    # bare-AppConfig fallback) whenever scitex-app is actually installed.
    assert resolved is RealScitexAppConfig


def _boot_django_for_storage_gui():
    """Shared helper -- NOT a test, does not itself need AAA markers."""
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_storage._django.settings")
    import django

    django.setup()


def test_storage_config_app_defaults_true_when_django_is_installed():
    # Arrange — boot Django against the GUI's own standalone settings.
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    # Act
    cfg = apps.get_app_config("scitex_storage_django")

    # Assert — the collision-avoidance contract hub relies on (see apps.py).
    assert cfg.default is True


def test_storage_config_app_slug_matches_manifest_when_django_is_installed():
    # Arrange — boot Django against the GUI's own standalone settings.
    pytest.importorskip("django")
    pytest.importorskip("scitex_app._django")
    _boot_django_for_storage_gui()
    from django.apps import apps

    # Act
    cfg = apps.get_app_config("scitex_storage_django")

    # Assert
    assert cfg.app_slug == "storage"


# EOF
