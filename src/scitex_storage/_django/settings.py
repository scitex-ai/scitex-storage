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
import tempfile
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

# SQLite lives in the temp dir so local runs don't pollute the project
_DB_DIR = Path(tempfile.gettempdir()) / "scitex_storage_gui"
_DB_DIR.mkdir(parents=True, exist_ok=True)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(_DB_DIR / "db.sqlite3"),
    }
}

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# EOF
