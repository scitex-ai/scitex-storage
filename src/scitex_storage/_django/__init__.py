#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/__init__.py
"""scitex-storage's Django app: a minimal GUI plugin for scitex-hub.

This package makes scitex-storage installable as a plugin into
scitex-hub (the ecosystem's central Django web app), following the same
scitex-app / scitex-ui convention already used by scitex-writer,
scitex-scholar, scitex-todo, and figrecipe.

**This is a first scaffold**, not the full disk-treemap UI: its purpose
is to prove the hub mounting contract works end-to-end (a real Django
app, with a real manifest, serving one page of real scan() data) — not
to ship a polished product yet.

Layout:

* ``_app_adapter.py`` — the ONLY place in this package allowed to import
  the (currently private) ``scitex_app._django`` / ``scitex_app._standalone``
  modules. See that module's docstring for why.
* ``apps.py`` — ``StorageConfig``, the Django ``AppConfig`` hub imports by
  its explicit dotted path.
* ``manifest.json`` — the app-plugin manifest scitex-app's validator reads.
* ``urls.py`` / ``views.py`` — one namespaced route, one view, real data.
* ``templates/scitex_storage/index.html`` — extends scitex-ui's shared
  workspace shell.
* ``_server.py`` — standalone local-dev launcher (``scitex-storage start-gui``).

None of this is imported by scitex-storage's core CLI package (see
``pyproject.toml``'s ``[project.optional-dependencies] gui`` extra) —
``pip install scitex-storage`` never requires Django, scitex-app, or
scitex-ui; only ``pip install scitex-storage[gui]`` does.
"""

from __future__ import annotations

# EOF
