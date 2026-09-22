#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_standalone_urls.py
"""Root URLconf for standalone local-dev (``scitex-storage start-gui``).

Cloud/hub deployments do not use this — hub includes
``scitex_storage._django.urls`` directly under its own ``storage/``
prefix (see ``urls.py``'s docstring). Mirrors
``scitex_writer._django._standalone_urls``.
"""

# Django is the OPTIONAL ``gui`` extra (see pyproject.toml). Guarded per
# PS-233/PS-148.
try:
    from django.urls import include, path
except ImportError as exc:
    raise ImportError(
        "scitex-storage URLs require Django: pip install scitex-storage[gui]"
    ) from exc

urlpatterns = [
    path("", include("scitex_storage._django.urls")),
]

# EOF
