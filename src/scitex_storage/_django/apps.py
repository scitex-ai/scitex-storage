#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/apps.py
"""Django AppConfig for the scitex-storage GUI plugin.

Imports ``ScitexAppConfig`` from the isolated ``_app_adapter`` module
(never directly from ``scitex_app._django`` — see that module's
docstring), so migrating to scitex-app's future public embedding API is
a one-line change in one file, not a codebase-wide edit.

``label`` is deliberately the unmistakable, fully-namespaced
``scitex_storage_django`` rather than a short generic name like
``storage`` — scitex-hub found a real collision bug on scitex-writer's
own app: mounting a bare module path (instead of the explicit
``AppConfig`` dotted path) let two apps both fall back to the same
generic label, raising Django's ``ImproperlyConfigured`` at hub boot.
``default = True`` on this class is the other half of that fix: it
makes THIS class Django's unambiguous default AppConfig for the
``scitex_storage._django`` package, so even a hub PR that (by mistake)
mounts the bare module path still resolves to the same label as the
explicit path.
"""

from __future__ import annotations

from ._app_adapter import ScitexAppConfig


class StorageConfig(ScitexAppConfig):
    """AppConfig for scitex-storage's scitex-hub GUI plugin."""

    default = True
    name = "scitex_storage._django"
    label = "scitex_storage_django"
    verbose_name = "SciTeX Storage"


# EOF
