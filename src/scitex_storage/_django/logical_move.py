"""User-authorized logical-tier move adapter; no physical host names in the UI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

try:
    from django.conf import settings
    from django.http import HttpResponseForbidden
    from django.shortcuts import render
    from django.utils.module_loading import import_string
    from django.views.decorators.http import require_POST
except ImportError as exc:
    raise ImportError("Storage move GUI requires scitex-storage[gui]") from exc

from scitex_storage._logical_tiers import logical_tiers

from . import volumes


def _tiers(request):
    owned = volumes.resolve_user_volumes(request)
    tiers = logical_tiers(
        owned,
        bindings=getattr(settings, "SCITEX_STORAGE_TIER_BINDINGS", {}),
        rates=getattr(settings, "SCITEX_STORAGE_TIER_RATES", {}),
    )
    reachable = {
        status.volume.key
        for status in volumes.measure_all(owned)
        if status.status == volumes.REACHABLE
    }
    tiers = tuple(
        replace(
            tier,
            binding_kind=None,
            binding=None,
            unavailable_reason="This storage location is not reachable.",
        )
        if tier.binding_kind == "volume" and tier.binding not in reachable
        else replace(
            tier,
            binding_kind=None,
            binding=None,
            unavailable_reason="Mount this tier before moving files in the browser.",
        )
        if tier.binding_kind == "archive"
        else tier
        for tier in tiers
    )
    return owned, tiers


def _state_dir(request):
    """Host-chosen private persistence root; never accept a browser path."""
    provider = getattr(settings, "SCITEX_USER_HOME_PROVIDER", "")
    if provider:
        try:
            resolver = (
                import_string(provider) if isinstance(provider, str) else provider
            )
            home = resolver(request)
        except Exception as exc:
            raise PermissionError(
                "Your persistent storage could not be resolved."
            ) from exc
        if home is None:
            raise PermissionError("Your persistent storage is unavailable.")
        home = Path(home).resolve(strict=True)
    elif getattr(settings, "SCITEX_APP_MODE", "standalone") == "standalone":
        home = Path.home().resolve()
    else:
        raise PermissionError("Your persistent storage is not configured.")
    return home / ".scitex/scitex-storage/runtime/move-manifests"


def _relative(value):
    value = str(value or "").strip()
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\0" in value:
        raise ValueError("Choose a relative folder inside the selected tier.")
    return path


def _resolve(request, form, owned, tiers):
    """Authorize both roots and explicit project policy before any move I/O."""
    from . import organize, project_files

    by_name = {tier.name: tier for tier in tiers}
    src_tier = by_name.get(form.get("source_tier", ""))
    dst_tier = by_name.get(form.get("destination_tier", ""))
    if src_tier is None or dst_tier is None:
        raise ValueError("Choose the source and destination tiers.")
    if not src_tier.available or not dst_tier.available:
        raise PermissionError(
            "One of the selected tiers is unavailable to your account."
        )
    if src_tier.binding_kind != "volume" or dst_tier.binding_kind != "volume":
        raise ValueError(
            "This move needs both tiers mounted as authorized storage locations."
        )
    src_vol = volumes.find_volume(owned, src_tier.binding)
    dst_vol = volumes.find_volume(owned, dst_tier.binding)
    try:
        source = volumes.contained_dir(src_vol, str(_relative(form.get("source_path"))))
        target = volumes.contained_dir(
            dst_vol, str(_relative(form.get("destination_path")))
        )
    except volumes.OutsideVolume as exc:
        raise PermissionError("The folder is outside the selected tier.") from exc
    # Never move an entire assigned volume; it includes credentials/state/projects.
    if source == src_vol.path.resolve() or target == dst_vol.path.resolve():
        raise PermissionError("Choose a folder inside a tier, rather than its root.")
    scope, pscope = organize._scope_selection(request)
    if scope == "project":
        if pscope is None:
            raise PermissionError("Select a project you may edit.")
        editable = project_files.resolve_project_scope(request, require_edit=True)
        if editable is None or editable.project_dir != pscope.project_dir:
            raise PermissionError("Editing this project is not permitted.")
        root = pscope.project_dir.resolve()
        if source == root or root.is_relative_to(source):
            raise PermissionError(
                "Moving a registered project root requires a project-location "
                "update and is unavailable here."
            )
        if not source.is_relative_to(root):
            raise PermissionError("The source folder is outside the selected project.")
    # Neither scope may silently relocate a registered project or write into
    # another view-only project at the destination.
    try:
        from scitex_sdk.ui.project_scope import host_project_provider

        provider = host_project_provider()
        if (
            provider is None
            and getattr(settings, "SCITEX_APP_MODE", "standalone") != "standalone"
        ):
            raise PermissionError("Project permissions are unavailable on this host.")
        entries = provider.list_projects(request) if provider is not None else []
        store = project_files._storage_capability()
        for entry in entries:
            location = store.project_path(entry.id, request)
            if location:
                root = Path(location).resolve()
                if root == source or root.is_relative_to(source):
                    raise PermissionError(
                        "Moving a registered project root requires a project-location "
                        "update and is unavailable here."
                    )
                if (
                    source.is_relative_to(root) or target.is_relative_to(root)
                ) and store.can_write(entry.id, request) is not True:
                    raise PermissionError("Editing this project is not permitted.")
    except PermissionError:
        raise
    except Exception as exc:
        raise PermissionError("Project permissions could not be verified.") from exc
    return source, target, src_vol, dst_vol


def _context(request, form, tiers):
    from . import organize

    return {
        **organize._base_context("move"),
        **organize._scope_context(request, "move"),
        "logical_tiers": [tier.public_details() for tier in tiers],
        "move_form": {
            key: form.get(key, "")
            for key in (
                "source_tier",
                "source_path",
                "destination_tier",
                "destination_path",
                "transaction_id",
            )
        },
    }


def _render(request, context):
    return render(request, "scitex_storage/logical_move.html", context)


def move(request):
    """Read-only plan, with estimates from configured rates only."""
    from scitex_storage import plan_move

    try:
        owned, tiers = _tiers(request)
        context = _context(request, request.GET, tiers)
        if context["scope"] == "project" and context["pscope"] is None:
            return HttpResponseForbidden("Select an available project.")
        if "plan" in request.GET:
            source, target, src_vol, dst_vol = _resolve(
                request, request.GET, owned, tiers
            )
            plan = plan_move(source, target, state_dir=_state_dir(request))
            context.update(
                move_plan=plan,
                logical_tiers=[tier.public_details(plan.size_bytes) for tier in tiers],
                source_display=volumes.display_path(src_vol, request, source),
                destination_display=volumes.display_path(dst_vol, request, target),
            )
            context["move_form"]["transaction_id"] = plan.transaction_id
    except PermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except (ValueError, OSError):
        context = locals().get("context", {})
        context["move_error"] = (
            "The move could not be planned. "
            "Check the folders and storage configuration."
        )
        # Physical mount paths in exception strings must not escape into a page.
    return _render(request, context)


@require_POST
def move_apply(request):
    """Explicit, bounded execution; a timeout cancels the actual rsync group."""
    from scitex_storage import apply_move, plan_move
    from scitex_storage._transfer._move import MoveRecoveryRequired

    try:
        owned, tiers = _tiers(request)
        context = _context(request, request.POST, tiers)
        if request.POST.get("confirm") != "yes":
            context["move_error"] = "Confirm the verified move before applying it."
            return _render(request, context)
        source, target, src_vol, dst_vol = _resolve(request, request.POST, owned, tiers)
        plan = plan_move(
            source,
            target,
            state_dir=_state_dir(request),
            transaction_id=request.POST.get("transaction_id") or None,
        )
        result = apply_move(plan, timeout_s=120)
        context.update(
            move_result=result,
            source_display=volumes.display_path(src_vol, request, source),
            destination_display=volumes.display_path(dst_vol, request, target),
        )
    except PermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except MoveRecoveryRequired as exc:
        context["move_error"] = (
            "This transaction needs recovery. Check its status before retrying. "
            "A recorded phase alone does not show a running worker."
        )
        context["move_transaction"] = exc.manifest.transaction_id
    except (ValueError, OSError, RuntimeError):
        context = locals().get("context", {})
        context["move_error"] = (
            "The move did not complete. Check its recorded status before retrying. "
            "Existing files will not be overwritten."
        )
    return _render(request, context)


def move_status(request):
    """Read only this user's historical receipt, without exposing mount paths."""
    from scitex_storage import get_move_status

    try:
        owned, tiers = _tiers(request)
        context = _context(request, request.GET, tiers)
        status = get_move_status(
            request.GET.get("transaction", ""), state_dir=_state_dir(request)
        )
        matched = []
        for location in (status.source, status.destination):
            path = Path(location).resolve()
            volume = next(
                (v for v in owned if path.is_relative_to(v.path.resolve())), None
            )
            if volume is None:
                raise PermissionError("This move is not available to your account.")
            matched.append(volumes.display_path(volume, request, path))
        context.update(
            move_status=status,
            source_display=matched[0],
            destination_display=matched[1],
        )
    except PermissionError as exc:
        return HttpResponseForbidden(str(exc))
    except (ValueError, OSError, TypeError):
        return HttpResponseForbidden(
            "That transaction is not available to your account."
        )
    return _render(request, context)
