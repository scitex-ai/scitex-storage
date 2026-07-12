#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_app_adapter.py
"""Isolated adapter around scitex-app's PRIVATE embedding API.

WHY THIS FILE EXISTS (and why nothing else in scitex-storage should
import ``scitex_app._django`` / ``scitex_app._standalone`` directly):

As of 2026-07-12, scitex-app does not yet expose a PUBLIC API for the
Django ``AppConfig`` base class and the standalone-server launcher that
every scitex-hub plugin needs — only the underscore-prefixed (private)
``scitex_app._django`` / ``scitex_app._standalone`` modules provide
them. This is a known, already-tracked ecosystem boundary violation
(scitex-dev card ``scitex-app-embedding-api-needed-20260710``,
currently deferred, no active work) — scitex-writer, figrecipe, and
scitex-todo all hit the same gap and inherited the same debt by
importing the private modules directly from wherever they were needed.

scitex-dev's explicit guidance (2026-07-12, given directly to
scitex-storage): don't wait for the public API, but don't scatter the
private imports either — isolate them behind ONE adapter module now, so
that when scitex-app ships a real public API, migrating is a single
one-line change in *this* file, not a codebase-wide grep-and-replace.

Rule: nowhere else in scitex-storage should write
``from scitex_app._django import ...`` or
``from scitex_app._standalone import ...`` — always import from here
instead (``from scitex_storage._django._app_adapter import
ScitexAppConfig, run_standalone``).

Both scitex-app and scitex-ui are OPTIONAL dependencies of
scitex-storage (the ``gui`` extra in ``pyproject.toml`` — the core CLI
package never requires Django or these UI packages). If scitex-app
isn't installed, ``ScitexAppConfig`` falls back to a bare Django
``AppConfig`` (mirroring the exact fallback pattern scitex-writer's own
``apps.py`` uses) and ``run_standalone`` raises a clear
``ImportError`` only when actually called, never at import time.
"""

from __future__ import annotations

try:
    from scitex_app._django import ScitexAppConfig as _ScitexAppConfig
except ImportError:
    from django.apps import AppConfig as _ScitexAppConfig  # type: ignore[assignment]

ScitexAppConfig = _ScitexAppConfig


def run_standalone(*args, **kwargs):
    """Thin re-export of ``scitex_app._standalone.run_standalone``.

    Deferred (imported lazily, inside the call) rather than at module
    import time, so importing this adapter module never requires
    scitex-app to be installed — only actually launching the standalone
    server (``scitex-storage start-gui``) does. Raises ``ImportError`` with
    scitex-app's own message if it isn't installed.
    """
    from scitex_app._standalone import run_standalone as _run_standalone

    return _run_standalone(*args, **kwargs)


# EOF
