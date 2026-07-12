#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_standalone_urls.py
"""Root URLconf for standalone local-dev (``scitex-storage start-gui``).

Cloud/hub deployments do not use this — hub includes
``scitex_storage._django.urls`` directly under its own ``storage/``
prefix (see ``urls.py``'s docstring). Mirrors
``scitex_writer._django._standalone_urls``.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("scitex_storage._django.urls")),
]

# EOF
