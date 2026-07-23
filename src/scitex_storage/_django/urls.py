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
    path("fleet", views.fleet, name="fleet"),
    path("healthz", views.healthz, name="healthz"),
]

# EOF
