#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/settings.py
"""Minimal standalone Django settings for `scitex-storage start-gui`.

Used only by the standalone launcher (``_server.py``'s bare-runserver
fallback); hub deployments ignore this module entirely and mount
``scitex_storage._django.urls`` under their own prefix. Mirrors
``scitex_writer._django.settings`` / ``figrecipe._django.settings``.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.environ.get("SCITEX_STORAGE_DJANGO_SECRET") or secrets.token_urlsafe(32)
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "0.0.0.0", "testserver"]

# "hub" | "standalone" — the browser tab alone must distinguish the two
# (see writer's precedent, scitex-hub PR #357). These settings only boot
# the STANDALONE server (`scitex-storage start-gui`), so standalone is the
# default here; hub's own settings override this to "hub".
SCITEX_APP_MODE = os.environ.get("SCITEX_APP_MODE", "standalone")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "scitex_storage._django.apps.StorageConfig",
]

# Optional: scitex-ui supplies the workspace shell (template + CSS/JS assets)
try:
    import scitex_ui  # noqa: F401

    INSTALLED_APPS.append("scitex_ui")
except ImportError:
    pass

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "scitex_storage._django._standalone_urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                # Enables scitex-ui's element inspector (Alt+I / Ctrl+I) in the
                # standalone GUI, matching scitex-writer's settings.py. Verified
                # importable against the real scitex-ui 0.6.3 package (installed
                # in a throwaway venv while building this scaffold — see the PR
                # description) rather than guessed.
                "scitex_ui.context_processors.element_inspector",
            ],
        },
    },
]

# No DATABASES. This layer declares ZERO models -- ``views.py`` renders
# ``scitex_storage._measure._scan`` results straight off the filesystem, and
# there is no ``models.py`` anywhere under ``_django/``. Django reads an empty
# ``DATABASES`` as "this project has no database" (it installs the dummy
# backend for the ``default`` alias), and ``runserver``'s migration check
# returns early rather than erroring, so nothing here needs a connection.
#
# The block this replaces pointed a local dev GUI at a private on-disk file
# and ran ``migrate --run-syncdb`` on every launch to create tables that no
# code ever read. Re-pointing that at the fleet's PostgreSQL primary would be
# worse than deleting it: a `gui open` on any workstation would then write
# ``django_migrations`` / ``django_content_type`` into the fleet's live
# ``scitex`` database, and a GUI that only lists directories would suddenly
# need the primary reachable to start. Deleting is the honest fix.

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# EOF
