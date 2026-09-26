#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/urls.py
"""URL patterns for the scitex-storage GUI plugin.

scitex-hub mounts this module at ``path("storage/", include("scitex_storage._django.urls"))``.
Namespaced via ``app_name`` so hub's ``{% url 'scitex_storage:index' %}``
never collides with another app's route names.
"""

from django.urls import path

from . import views

app_name = "scitex_storage"

urlpatterns = [
    path("", views.index, name="index"),
    # Trailing slash is the Django convention; APPEND_SLASH then redirects
    # a bare ``/fleet`` here too, so both forms work. The operator hit a
    # 404 typing ``/fleet/`` against a slashless ``path("fleet", ...)``.
    path("fleet/", views.fleet, name="fleet"),
    path("bubbles/", views.bubbles, name="bubbles"),
    path("sunburst/", views.sunburst, name="sunburst"),
    path("healthz", views.healthz, name="healthz"),
    # Project-scoped file API (compass §14). Machine-facing JSON/attachment
    # routes; authz + project scope come from the hub via `request`. The
    # existing ``index`` (scan) route is unchanged — this slice adds the
    # project file surface next to it.
    path("api/list", views.project_list, name="project_list"),
    path("api/read", views.project_read, name="project_read"),
    path("api/download", views.project_download, name="project_download"),
    path("api/write", views.project_write, name="project_write"),
    path("api/rename", views.project_rename, name="project_rename"),
    path("api/delete", views.project_delete, name="project_delete"),
    path("api/mkdir", views.project_mkdir, name="project_mkdir"),
]

# EOF
