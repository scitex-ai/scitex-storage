#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/views.py
"""Views for the scitex-storage GUI plugin.

``index`` serves the organize tabs (Usage / Duplicates / Move, plus the
Backup placeholder) with a shared tab nav. There is deliberately no
machine/volume browser: per-project storage is the workspace's business,
and this app only reports usage, duplicates, and move plans.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET, require_POST

from scitex_ui.branding import shell_context

from . import organize, project_files
from .project_files import StorageFileError
from ._favicon import FAVICON_HREF

#: What each of the shell's three panes IS for this app, per scitex-ui's
#: ``shell_context(panes=...)`` contract. Declared, never inferred — only the
#: app knows which panes it uses, so the app says.
#:
#: All three are ``"unused"``, and that is a measurement rather than a guess:
#: ``templates/scitex_storage/index.html`` fills ONLY the ``app_content``
#: block and leaves ``extra_js`` EMPTY, so no pane is populated server-side
#: and none can be populated after mount either.
#:
#: ``files`` is the one worth justifying, because "a storage browser surely
#: uses the files pane" is the plausible wrong answer. It does not: the
#: directory listing is a server-rendered ``<table class="stx-storage-table">``
#: INSIDE ``app_content``. Declaring it ``"client-populated"`` would keep
#: ~490px reserved for a pane nothing ever fills.
#:
#: The other three routes (``fleet`` / ``bubbles`` / ``sunburst``) return
#: standalone ``HttpResponse`` HTML and never extend the shell, so the
#: declaration is deliberately scoped to ``index``.
SHELL_PANES = {"ai": "unused", "files": "unused", "viewer": "unused"}


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


#: Tabs of the storage app. Usage/Duplicates/Move are real views (see
#: :mod:`.organize`); only Backup is still a placeholder.
TABS = (
    ("usage", gettext_lazy("Usage"), False),
    ("move", gettext_lazy("Move"), False),
    ("backup", gettext_lazy("Backup"), True),
    ("duplicates", gettext_lazy("Duplicates"), False),
)


def _tab_context(active: str) -> list:
    return [
        {"key": key, "label": label, "soon": soon, "active": key == active}
        for key, label, soon in TABS
    ]


def index(request):
    """Organize tabs, defaulting to Usage. Unknown tabs fall back to Usage."""
    tab = request.GET.get("tab", "usage")
    if tab not in {key for key, _label, _soon in TABS}:
        tab = "usage"
    context = {
        **shell_context("Storage", panes=SHELL_PANES),
        "app_label": _app_label("SciTeX Storage"),
        "favicon_href": FAVICON_HREF,
        "tabs": _tab_context(tab),
        "tab": tab,
    }
    if tab in organize.HANDLED_TABS:
        return organize.serve(request, tab)
    return render(request, "scitex_storage/index.html", context)


def fleet(request) -> HttpResponse:
    """Serve the cached multi-host fleet dashboard.

    Reads a snapshot rendered OUT OF BAND (``_observe.write_fleet_snapshot``)
    and never gathers live: ``observe_fleet`` ssh-probes six hosts and
    takes ~90s, which would hang this request handler exactly as a
    scan-on-load hangs ``index`` (see that view's docstring). The gather
    is a periodic job; this view only reads its output.

    When no snapshot exists yet, returns a plain, honest placeholder that
    names the command to produce one -- NOT a blank page and NOT a 500,
    because "not gathered yet" is a real state a first-run user will hit.
    """
    from scitex_storage._observe import (
        default_snapshot_path,
        fleet_html_or_placeholder,
    )

    return HttpResponse(fleet_html_or_placeholder(default_snapshot_path()))


def bubbles(request) -> HttpResponse:
    """Serve the cached interactive capacity-bubble page.

    Same cache-read discipline as :func:`fleet`: rendered out of band from
    the fleet snapshot, served verbatim here, placeholder when absent.
    """
    from scitex_storage._observe import (
        default_bubbles_path,
        fleet_html_or_placeholder,
    )

    return HttpResponse(fleet_html_or_placeholder(default_bubbles_path()))


def sunburst(request) -> HttpResponse:
    """Serve the cached interactive capacity-sunburst page (Codecov-style).

    Same cache-read discipline as :func:`fleet`: rendered out of band,
    served verbatim, placeholder when absent.
    """
    from scitex_storage._observe import (
        default_sunburst_path,
        fleet_html_or_placeholder,
    )

    return HttpResponse(fleet_html_or_placeholder(default_sunburst_path()))


def healthz(request) -> HttpResponse:
    """Trivial liveness check — not part of the manifest'd UI routes."""
    return HttpResponse("ok")


# --------------------------------------------------------------------------- #
# Project-scoped file API (compass §14: List + Read + Download)
# --------------------------------------------------------------------------- #
# Thin adapters over ``project_files``. The authz and project-scope come from
# the hub via ``request`` (see project_files' module docstring); the file
# primitives come from ``scitex_app.sdk.get_files``. No second user/project
# model lives here or in project_files.
#
# Every handler catches ``StorageFileError`` and maps it to its typed HTTP
# status via ``STATUS_BY_CODE`` — a project-file failure is a 404/403/400/507,
# NEVER a bare 500. The existing ``scan`` views keep their own error handling
# (browser-facing page); these are machine-facing API routes.
# --------------------------------------------------------------------------- #
def _json_error(exc: StorageFileError) -> HttpResponse:
    from django.http import JsonResponse

    return JsonResponse(exc.to_payload(), status=exc.status)


@require_GET
def project_list(request) -> HttpResponse:
    """List one directory level of the current project.

    ``?path=`` is RELATIVE to the project root (never absolute — an absolute
    ``?path=`` resolves outside the root and is denied as ``permission_denied``
    before any filesystem access).
    """
    from django.http import JsonResponse

    rel = request.GET.get("path", "")
    try:
        payload = project_files.list_files(request, rel)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


@require_GET
def project_read(request) -> HttpResponse:
    """Read one text file from the current project (``?path=``)."""
    from django.http import JsonResponse

    rel = request.GET.get("path", "")
    try:
        payload = project_files.read_file(request, rel)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


@require_GET
def project_download(request) -> HttpResponse:
    """Stream one file from the current project as an attachment (``?path=``)."""
    rel = request.GET.get("path", "")
    try:
        return project_files.download_file(request, rel)
    except StorageFileError as exc:
        return _json_error(exc)


@require_POST
def project_write(request) -> HttpResponse:
    """Write one text file into the current project (POST ``{path, content}``).

    The write half of compass \u00a714 L493. Scope + authz come from the hub
    resolver via ``request``; containment (traversal, symlink-out, under-file)
    is enforced in ``project_files._contained_for_write``; the write is atomic
    (temp + ``os.replace``) so a mid-write failure never leaves a partial file.
    Failures map to typed 4xx/507 via :data:`STATUS_BY_CODE` -- never a bare 500.
    """
    from django.http import JsonResponse

    body = _parse_json_body(request)
    if isinstance(body, dict) and "error" in body:
        return JsonResponse(body, status=body.pop("status", 400))
    rel = body.get("path", "")
    content = body.get("content")
    if not rel or content is None:
        return JsonResponse(
            {"error": "invalid_path", "message": "path and content are required"},
            status=400,
        )
    try:
        payload = project_files.write_file(request, rel, content)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


def _parse_json_body(request) -> dict:
    import json

    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {"error": "invalid_path", "message": "invalid JSON body", "status": 400}
    if not isinstance(data, dict):
        return {"error": "invalid_path", "message": "JSON body must be an object", "status": 400}
    return data


@require_POST
def project_rename(request) -> HttpResponse:
    """Rename / move one file within the current project.

    POST ``{old_path, new_path}``. Covers both the Rename and Move halves of
    compass \u00a714 L494 (the SDK's ``rename`` is the move primitive). Scope +
    authz from the hub resolver; containment on BOTH paths; an existing
    destination is a typed ``file_conflict`` (409). Failures map via
    :data:`STATUS_BY_CODE` -- never a bare 500.
    """
    from django.http import JsonResponse

    body = _parse_json_body(request)
    if isinstance(body, dict) and "error" in body:
        return JsonResponse(body, status=body.pop("status", 400))
    old_rel = body.get("old_path", "")
    new_rel = body.get("new_path", "")
    if not old_rel or not new_rel:
        return JsonResponse(
            {"error": "invalid_path", "message": "old_path and new_path are required"},
            status=400,
        )
    try:
        payload = project_files.rename_file(request, old_rel, new_rel)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


@require_POST
def project_delete(request) -> HttpResponse:
    """Delete one file, symlink, or directory from the current project.

    POST ``{path}``. Directories are removed recursively. A final symlink is
    unlinked without following its referent, symlinked parent components are
    refused, and the project root is never a valid target. Scope + authz come
    from the hub resolver.
    """
    from django.http import JsonResponse

    body = _parse_json_body(request)
    if isinstance(body, dict) and "error" in body:
        return JsonResponse(body, status=body.pop("status", 400))
    rel = body.get("path", "")
    if not rel:
        return JsonResponse(
            {"error": "invalid_path", "message": "path is required"},
            status=400,
        )
    try:
        payload = project_files.delete_file(request, rel)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


@require_POST
def project_mkdir(request) -> HttpResponse:
    """Create one (optionally nested) directory in the current project.

    POST ``{path}``. The create half of compass line 495 (folder operations).
    A new directory -> 200; an existing directory -> ``file_conflict`` (409);
    an existing file at the path -> ``not_a_file`` (400); a containment
    escape (``..`` / absolute / symlink-out / under-file) -> ``permission_
    denied`` (403). Scope + authz from the hub resolver.
    """
    from django.http import JsonResponse

    body = _parse_json_body(request)
    if isinstance(body, dict) and "error" in body:
        return JsonResponse(body, status=body.pop("status", 400))
    rel = body.get("path", "")
    if not rel:
        return JsonResponse(
            {"error": "invalid_path", "message": "path is required"},
            status=400,
        )
    try:
        payload = project_files.make_dir(request, rel)
    except StorageFileError as exc:
        return _json_error(exc)
    return JsonResponse(payload)


# EOF
