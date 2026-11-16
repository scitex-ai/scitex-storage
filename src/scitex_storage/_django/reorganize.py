#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reorganize tab: apply a YAML move plan inside ONE authorized folder.

The browser front of ``scitex-storage relocate``. Flow: Preview → Apply → Undo.

SECURITY (same contract as the Move tab):
- No free-form paths. The folder is a volume key from the requester's own
  volumes plus a relative path, containment-checked (``contained_dir``).
  A volume root itself is refused (it holds credentials, state and projects).
- In project scope the folder must lie inside a project the requester may
  EDIT. In all scope, a folder containing a registered project root is
  refused, because a move could silently relocate that project.
- Plan paths are relative to the folder by construction (the engine rejects
  absolute paths and ``..``), so nothing can leave it.
- Apply re-plans and compares the fingerprint with the previewed one, so it
  never runs operations the user did not see.
- Pages show folder-relative paths only, never physical mount paths.
- Run manifests live in the requester's jailed home (as Move receipts do).
"""

from __future__ import annotations

from pathlib import Path

try:
    from django.http import HttpResponse, HttpResponseForbidden
    from django.shortcuts import render
    from django.views.decorators.http import require_POST
except ImportError as exc:
    raise ImportError(
        "scitex-storage reorganize views require Django: pip install scitex-storage[gui]"
    ) from exc

from . import volumes
from .logical_move import StorageConfigurationError, _relative, _state_dir

#: A plan is a short YAML document; anything bigger is not a hand-made plan.
MAX_PLAN_BYTES = 200_000
#: Rows shown in the preview table; counts always cover the whole plan.
MAX_PREVIEW_ROWS = 300

TEMPLATE = "scitex_storage/reorganize.html"


def _manifest_dir(request) -> Path:
    return _state_dir(request).parent / "relocate-manifests"


def _parse_plan(text: str) -> dict:
    from scitex_storage._transfer._relocate import load_plan_text

    if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
        raise ValueError("The plan is too large.")
    return load_plan_text(text)


def _registered_project_roots(request) -> list[Path]:
    """Roots of every project the host knows; [] in a plain standalone app."""
    from django.conf import settings

    from . import project_files

    try:
        from scitex_sdk.ui.project_scope import host_project_provider
    except ImportError:
        host_project_provider = None
    provider = host_project_provider() if host_project_provider else None
    if provider is None:
        if getattr(settings, "SCITEX_APP_MODE", "standalone") != "standalone":
            raise PermissionError("Project permissions are unavailable on this host.")
        return []
    store = project_files._storage_capability()
    if isinstance(store, project_files._ClosedStorage):
        raise PermissionError("Project permissions are unavailable on this host.")
    roots = []
    for entry in provider.list_projects(request):
        location = store.project_path(entry.id, request)
        if not location or not Path(location).is_absolute():
            raise PermissionError("Project locations could not be verified.")
        roots.append(Path(location).resolve())
    return roots


def _authorize_folder(request, form, owned):
    """Volume key + relative folder -> (volume, absolute folder), or raise."""
    from . import organize, project_files

    volume = volumes.find_volume(owned, form.get("volume", ""))
    if volume is None:
        raise PermissionError("Choose one of your storage locations.")
    try:
        folder = volumes.contained_dir(volume, str(_relative(form.get("folder"))))
    except volumes.OutsideVolume as exc:
        raise PermissionError("The folder is outside the selected location.") from exc
    if folder == volume.path.resolve():
        raise PermissionError(
            "Choose a folder inside a location, rather than its root."
        )
    if not folder.is_dir():
        raise ValueError("That folder does not exist.")
    scope, pscope = organize._scope_selection(request)
    if scope == "project":
        if pscope is None:
            raise PermissionError("Select a project you may edit.")
        try:
            editable = project_files.resolve_project_scope(request, require_edit=True)
        except project_files.PermissionDenied as exc:
            raise PermissionError("Editing this project is not permitted.") from exc
        if editable is None or editable.project_dir != pscope.project_dir:
            raise PermissionError("Editing this project is not permitted.")
        if not folder.is_relative_to(pscope.project_dir.resolve()):
            raise PermissionError("The folder is outside the selected project.")
    else:
        try:
            roots = _registered_project_roots(request)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError("Project permissions could not be verified.") from exc
        for root in roots:
            if root == folder or root.is_relative_to(folder):
                raise PermissionError(
                    "This folder contains a registered project. Reorganize it "
                    "from that project's scope instead."
                )
    return volume, folder


def _context(request, form, owned, extra=None):
    from . import organize

    ctx = {
        **organize._base_context("reorganize"),
        **organize._scope_context(request, "reorganize"),
        # Logical names only: a volume label can be a physical host name.
        "reorg_volumes": [
            {"key": v.key, "label": volumes.display_path(v, request)} for v in owned
        ],
        "reorg_form": {
            "volume": form.get("volume", ""),
            "folder": form.get("folder", ""),
            "plan": form.get("plan", ""),
            "fingerprint": form.get("fingerprint", ""),
        },
    }
    ctx.update(extra or {})
    return ctx


def _summary(plan) -> dict:
    return {
        "moves": plan.count("move"),
        "duplicates": plan.count("archive", "duplicate"),
        "junk": plan.count("archive", "junk"),
        "drops": plan.count("archive", "drop"),
        "links": plan.count("link"),
        "conflicts": len(plan.conflicts),
    }


def reorganize(request):
    """GET: the empty form (rendered through the organize tab router)."""
    try:
        owned = volumes.resolve_user_volumes(request)
        context = _context(request, request.GET, owned)
    except StorageConfigurationError:
        return HttpResponse("Storage configuration is unavailable.", status=503)
    return render(request, TEMPLATE, context)


@require_POST
def reorganize_action(request):
    """POST action=preview|apply|undo. Every action re-authorizes the folder."""
    import time

    from scitex_storage._cli._reclaim_cmd import _run_id
    from scitex_storage._transfer._relocate import (
        RelocateConflict,
        apply_relocate,
        plan_fingerprint,
        plan_relocate,
        restore_relocate,
    )

    action = request.POST.get("action", "")
    context: dict = {}
    try:
        owned = volumes.resolve_user_volumes(request)
        context = _context(request, request.POST, owned)
        if action == "undo":
            run_id = request.POST.get("run_id", "")
            manifest_dir = _manifest_dir(request)
            from scitex_storage._transfer._relocate import load_manifest

            m = load_manifest(run_id, manifest_dir=manifest_dir)
            if not any(Path(m.root).is_relative_to(v.path.resolve()) for v in owned):
                raise PermissionError("This run is not available to your account.")
            restore_relocate(run_id, manifest_dir=manifest_dir)
            context["reorg_restored"] = run_id
            return render(request, TEMPLATE, context)

        volume, folder = _authorize_folder(request, request.POST, owned)
        spec = _parse_plan(request.POST.get("plan", ""))
        plan = plan_relocate(spec, folder, run_id=_run_id(time.time()))
        fingerprint = plan_fingerprint(plan)
        context.update(
            reorg_plan=plan,
            reorg_rows=plan.ops[:MAX_PREVIEW_ROWS],
            reorg_truncated=len(plan.ops) > MAX_PREVIEW_ROWS,
            reorg_summary=_summary(plan),
            reorg_folder_display=volumes.display_path(volume, request, folder),
        )
        context["reorg_form"]["fingerprint"] = fingerprint
        if action == "apply":
            if request.POST.get("confirm") != "yes":
                context["reorg_error"] = (
                    "Confirm the reorganization before applying it."
                )
            elif request.POST.get("fingerprint", "") != fingerprint:
                context["reorg_error"] = (
                    "The folder or the plan changed since the preview. Review the "
                    "new preview before applying."
                )
            elif plan.conflicts:
                context["reorg_error"] = "Resolve the conflicts before applying."
            else:
                apply_relocate(plan, manifest_dir=_manifest_dir(request))
                context["reorg_applied"] = plan.run_id
        elif action != "preview":
            context["reorg_error"] = "Unknown action."
    except StorageConfigurationError:
        return HttpResponse("Storage configuration is unavailable.", status=503)
    except PermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except RelocateConflict:
        context["reorg_error"] = "Resolve the conflicts before applying."
    except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
        context = locals().get("context", {})
        # Engine messages name only plan-relative paths; mount paths never reach the page.
        message = str(exc)
        context["reorg_error"] = (
            message
            if message and not message.startswith("/")
            else "The plan could not be processed. Check the plan and the folder."
        )
    return render(request, TEMPLATE, context)


# EOF
