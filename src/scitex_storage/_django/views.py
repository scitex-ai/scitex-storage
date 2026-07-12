#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/views.py
"""Views for the scitex-storage GUI plugin.

This is the first scaffold's "real data" proof: ``index`` calls
scitex-storage's EXISTING, already-tested ``scan()`` (from
``scitex_storage._scan``) against a real path and renders the result as
an HTML table — not a placeholder. The full disk-treemap UI is a later
phase (see the ``scitex-storage-gui-plugin-for-scitex-hub`` scitex-todo
card).

``?path=`` lets the caller point the scan anywhere; it defaults to the
current user's home directory, which is always a safe, readable,
bounded starting point (never ``/`` — a full-filesystem scan is exactly
the kind of thing this tool is designed to avoid doing by accident).
"""

from __future__ import annotations

from pathlib import Path

from django.http import HttpResponse
from django.shortcuts import render

from scitex_storage._report import format_count, format_size
from scitex_storage._scan import MissingSystemDependencyError, scan


def _app_label(base: str) -> str:
    """Tab title per the fleet ``SCITEX_APP_MODE`` convention.

    Mirrors scitex-writer's ``_app_label`` helper: the browser tab alone
    must distinguish hub-embedded from standalone. Reads the Django
    setting configured by ``settings.py`` / ``_server.py``, defaulting
    to "standalone" (hub's mount overrides it to "hub").
    """
    from django.conf import settings

    mode = getattr(settings, "SCITEX_APP_MODE", "standalone")
    return f"{base} (hub)" if mode == "hub" else base


def index(request):
    """Render one directory's ``scan()`` result as an HTML table.

    Query params:
        path: directory to scan (default: the user's home directory).

    Errors (missing path, not a directory, ``fd`` not installed) render
    the same template with an ``error`` message instead of raising a
    bare 500 — this is a browser-facing page, not an API.
    """
    raw_path = request.GET.get("path") or str(Path.home())
    context = {
        "app_label": _app_label("SciTeX Storage"),
        "requested_path": raw_path,
        "error": None,
        "root": None,
        "children": [],
        "total_size": None,
        "total_files": None,
    }

    try:
        result = scan(raw_path)
    except MissingSystemDependencyError as e:
        context["error"] = (
            "scitex-storage scan requires the `fd` binary, which is not "
            f"installed on this server: {e}"
        )
        return render(request, "scitex_storage/index.html", context)
    except (FileNotFoundError, NotADirectoryError) as e:
        context["error"] = str(e)
        return render(request, "scitex_storage/index.html", context)

    ordered = result.by_size()
    context["root"] = str(result.root)
    context["total_size"] = format_size(result.total_size)
    context["total_files"] = format_count(result.total_files)
    context["children"] = [
        {
            "name": c.name + ("/" if c.is_dir else ""),
            "size": format_size(c.size),
            "file_count": format_count(c.file_count),
            "error": c.error,
        }
        for c in ordered
    ]
    return render(request, "scitex_storage/index.html", context)


def healthz(request) -> HttpResponse:
    """Trivial liveness check — not part of the manifest'd UI routes."""
    return HttpResponse("ok")


# EOF
