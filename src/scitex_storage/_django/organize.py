#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Organize views for the Storage app: Usage / Duplicates / Move tabs.

This module implements the three organize tabs (Backup stays a ``[Soon]``
placeholder in :mod:`.views`), backed by the ``scitex-storage`` Python API (ported from the scitex-hub mount glue, which
used to carry them hub-side):

* Usage — donut breakdown from ``volumes.measure_all`` (statvfs, always
  available) plus a per-folder donut from ``scitex_storage.scan`` when the
  ``fd`` binary is present.
* Duplicates — read-only report from ``find_duplicates`` / ``reclaimable_bytes``.
  The API has no delete/resolve verb (``fclones group`` only reports), so this
  tab never offers one. Needs the ``fclones`` binary; otherwise an honest
  disabled-with-reason panel.
* Move — tier-to-tier planning via ``plan_archive`` (local -> scitex-nas-0x,
  verified before anything is removed, manifest for restore). Planning is
  read-only; applying needs rsync+ssh operator setup and an explicit
  ``--yes`` confirm, so the web UI plans only and points at the CLI for the
  gated apply. Never deletes data from the browser.

SECURITY: no free-form paths anywhere. Scan roots come only from the
requester's own volumes (``find_volume`` by key); subdirectories are
containment-checked with the upstream ``contained_dir`` helper (raises
``OutsideVolume`` -> 403). Unknown volume keys fail closed with 403.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Callable

# Django is the OPTIONAL ``gui`` extra (see pyproject.toml). Guarded per
# PS-233/PS-148 so a GUI import without the extra names the remedy instead
# of a bare ModuleNotFoundError.
try:
    from django.http import HttpResponseForbidden
    from django.shortcuts import render
    from django.utils.translation import gettext_lazy as _
except ImportError as exc:
    raise ImportError(
        "scitex-storage organize views require Django: "
        "pip install scitex-storage[gui]"
    ) from exc

#: Tabs this module serves. ``backup`` stays upstream (still [Soon] there).
HANDLED_TABS = ("usage", "duplicates", "move")

ORGANIZE_TABS = (
    ("usage", _("Usage"), False),
    ("move", _("Move"), False),
    ("backup", _("Backup"), True),
    ("duplicates", _("Duplicates"), False),
)

#: Bound for the opt-in duplicate walk (fclones --depth). Unbounded content
#: hashing from a web request is how you melt a login node.
DUP_MAX_DEPTH = 4

#: Seconds before a worker thread's answer stops being worth waiting for.
SCAN_TIMEOUT_S = 15.0
DUPLICATES_TIMEOUT_S = 60.0
PLAN_TIMEOUT_S = 20.0

#: Donut geometry (r=70 in a 160x160 viewBox) + a fixed segment palette.
DONUT_R = 70.0
DONUT_C = 2 * math.pi * DONUT_R
DONUT_COLORS = (
    "#58a6ff",
    "#3fb950",
    "#d29922",
    "#f778ba",
    "#bc8cff",
    "#ffa657",
    "#79c0ff",
    "#ffa198",
)

SHELL_PANES = {"ai": "unused", "files": "unused", "viewer": "unused"}


@dataclass
class DonutSegment:
    label: str
    value: int
    fraction: float
    dash: float
    offset: float
    color: str


def donut_segments(items: list[tuple[str, int]]) -> list[DonutSegment]:
    """Turn ``(label, bytes)`` pairs into SVG circle-segment parameters.

    Pure function (no I/O) so the geometry is unit-testable. Zero/negative
    values are dropped; an empty input yields no segments (the template
    renders a muted empty ring instead).
    """
    kept = [(label, v) for label, v in items if v > 0]
    total = sum(v for _label, v in kept)
    if not kept or total <= 0:
        return []
    segments: list[DonutSegment] = []
    start = 0.0
    for i, (label, value) in enumerate(kept):
        fraction = value / total
        segments.append(
            DonutSegment(
                label=label,
                value=value,
                fraction=fraction,
                dash=fraction * DONUT_C,
                offset=-start * DONUT_C,
                color=DONUT_COLORS[i % len(DONUT_COLORS)],
            )
        )
        start += fraction
    return segments


def _run_bounded(fn: Callable[[], Any], timeout_s: float) -> tuple[bool, Any]:
    """Run ``fn()`` in a daemon thread; ``(True, value)`` or ``(False, error)``.

    Long scans must never hang the request handler (same discipline as the
    upstream ``volumes.measure_all`` deadline). ``fn`` raising gives
    ``(False, exc)``; a missed deadline gives ``(False, TimeoutError)``.
    """
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — reported, never raised
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return False, TimeoutError(f"timed out after {timeout_s:g}s")
    if "error" in box:
        return False, box["error"]
    return True, box.get("value")


def _base_context(active: str) -> dict:
    """Shell + tab-nav context shared by the three organize tabs."""
    try:
        from scitex_sdk.ui.branding import shell_context
    except ImportError as exc:
        raise ImportError(
            "scitex-storage organize views require scitex-sdk UI: "
            "pip install scitex-storage[gui]"
        ) from exc

    from . import views as _views
    from ._favicon import FAVICON_HREF

    return {
        **shell_context("Storage", panes=_views.SHELL_PANES),
        **_views._leaf_identity(),
        "app_label": _views._app_label("SciTeX Storage"),
        "favicon_href": FAVICON_HREF,
        "project_picker_available": _views._project_picker_available(),
        "tabs": [
            {"key": key, "label": label, "soon": soon, "active": key == active}
            for key, label, soon in ORGANIZE_TABS
        ],
        "tab": active,
    }


def _user_volumes(request):
    from . import volumes

    return volumes.resolve_user_volumes(request)


def _scope_selection(request):
    """("all"|"project", ProjectScope|None) for the organize tabs.

    Header-driven: the shared app header already selects the project, so
    a resolved header scope means project scope -- no second control.
    Never raises: anything unresolvable reads as no-project-in-scope.
    An explicit ``?scope=project`` with nothing in the header resolves to
    ("project", None) so the views render the pick-in-header notice.
    """
    from . import project_files

    try:
        pscope = project_files.resolve_project_scope(request)
    except Exception:
        pscope = None
    if pscope is not None:
        return "project", pscope
    if request.GET.get("scope") == "project":
        return "project", None
    return "all", None


def _scope_urls(request, tab):
    """(all_url, project_url) preserving the tab and the other GET params."""
    from urllib.parse import urlencode

    keep = [(k, v) for k, v in request.GET.items() if k not in ("tab", "scope")]
    base = urlencode([("tab", tab)] + keep)
    return f"?{base}", f"?{base}&scope=project"


def _scope_context(request, tab):
    """Shared scope-control context for the organize tabs."""
    scope, pscope = _scope_selection(request)
    all_url, project_url = _scope_urls(request, tab)
    notice = ""
    name = ""
    if scope == "project":
        if pscope is None:
            notice = str(
                _("No project in scope -- showing user space. Pick a project "
                  "in the header to filter these tabs to it.")
            )
        else:
            name = pscope.name or pscope.slug or str(_("this project"))
    return {
        "scope": scope,
        "show_scope": True,
        "scope_all_url": all_url,
        "scope_project_url": project_url,
        "scope_qs": "&scope=project" if scope == "project" else "",
        "project_name": name,
        "scope_notice": notice,
        "pscope": pscope,
    }


def _within(path, root) -> bool:
    """True when ``path`` sits inside ``root`` (both resolved)."""
    from pathlib import Path

    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _selected_volume(request, user_volumes, statuses):
    """Pick a volume by ``?volume=`` key, defaulting to the first reachable.

    Unknown keys fail closed (None -> the caller renders 403); never falls
    back to a default when a key was explicitly given.
    """
    from . import volumes

    key = request.GET.get("volume")
    if key:
        return volumes.find_volume(user_volumes, key)
    for st in statuses:
        if st.status == volumes.REACHABLE and st.total:
            return st.volume
    return user_volumes[0] if user_volumes else None


def _fleet_views_available() -> bool:
    """True when a gathered fleet snapshot exists to link to.

    Imported lazily so the organize tabs never hard-require the observation
    layer at module import; any failure reads as \"no snapshot\" rather than
    a 500, because \"not gathered yet\" is a real first-run state.
    """
    try:
        from scitex_storage._observe import fleet_views_available
    except ImportError:
        return False
    try:
        return bool(fleet_views_available())
    except Exception:
        return False


def usage(request):
    """Donut breakdown of where the requester's bytes live."""
    from . import volumes

    user_volumes = _user_volumes(request)
    statuses = volumes.measure_all(user_volumes)

    reachable = [st for st in statuses if st.status == volumes.REACHABLE and st.total]
    fleet_items = [(st.volume.label, st.used) for st in reachable]
    free_total = sum(st.free for st in reachable)
    if free_total > 0:
        fleet_items.append((str(_("Free")), free_total))

    folder_items: list[tuple[str, int]] = []
    folder_note = ""
    folder_volume = _selected_volume(request, user_volumes, statuses)
    if request.GET.get("volume") and folder_volume is None:
        return HttpResponseForbidden(str(_("That volume is not yours.")))
    scope_ctx = _scope_context(request, "usage")
    pscope = scope_ctx["pscope"]
    folder_label = folder_volume.label if folder_volume is not None else ""
    scan_root = None
    if scope_ctx["scope"] == "project" and pscope is not None:
        # Project scope wins over the volume picker: the breakdown answers
        # "where do THIS project's bytes live", not "what is on the volume".
        scan_root = pscope.project_dir
        folder_label = scope_ctx["project_name"]
    if folder_volume is not None or scan_root is not None:
        try:
            from scitex_storage import scan as _scan
        except ImportError:
            _scan = None
        if _scan is None:
            folder_note = str(
                _("Per-folder breakdown needs the scitex-storage package.")
            )
        else:
            root = scan_root if scan_root is not None else folder_volume.path
            ok, result = _run_bounded(lambda: _scan(root), SCAN_TIMEOUT_S)
            if ok:
                top = result.by_size()[:8]
                folder_items = [(c.name, c.size) for c in top]
                rest = result.by_size()[8:]
                rest_bytes = sum(c.size for c in rest)
                if rest_bytes > 0:
                    folder_items.append((str(_("Other")), rest_bytes))
                if result.children and not folder_items:
                    folder_note = str(_("Everything under this volume is empty."))
            else:
                err = result
                name = type(err).__name__
                if name == "MissingSystemDependencyError":
                    folder_note = str(
                        _(
                            "Per-folder breakdown is unavailable here: "
                            "the `fd` binary is not installed on this host. "
                            "Volume totals above are unaffected."
                        )
                    )
                elif isinstance(err, TimeoutError):
                    folder_note = str(
                        _(
                            "Per-folder scan timed out; volume totals above are unaffected."
                        )
                    )
                else:
                    folder_note = str(
                        _("Per-folder scan failed: %(err)s") % {"err": err}
                    )

    context = {
        **_base_context("usage"),
        **scope_ctx,
        "fleet_segments": donut_segments(fleet_items),
        "fleet_total": sum(v for _label, v in fleet_items),
        "statuses": statuses,
        "reachable": volumes.REACHABLE,
        "folder_volume": folder_volume,
        "folder_label": folder_label,
        "folder_segments": donut_segments(folder_items),
        "folder_total": sum(v for _label, v in folder_items),
        "folder_note": folder_note,
        "fleet_available": _fleet_views_available(),
    }
    return render(request, "scitex_storage/organize.html", context)


def duplicates(request):
    """Read-only duplicate report. No resolve/delete action exists by design."""
    from . import volumes

    user_volumes = _user_volumes(request)
    statuses = volumes.measure_all(user_volumes)
    roots = [
        st.volume.path
        for st in statuses
        if st.status == volumes.REACHABLE and st.volume.path.is_dir()
    ]
    scope_ctx = _scope_context(request, "duplicates")
    pscope = scope_ctx["pscope"]
    if scope_ctx["scope"] == "project" and pscope is not None:
        # Duplicates within the project only: same report, narrower roots.
        roots = [pscope.project_dir]

    groups: list[dict] = []
    reclaimable: int | None = None
    unavailable_reason = ""
    scanned = False
    if not roots:
        unavailable_reason = str(_("No reachable volumes to scan."))
    else:
        try:
            from scitex_storage import find_duplicates as _find
            from scitex_storage._measure._duplicates import (
                reclaimable_bytes as _reclaimable,
            )
        except ImportError:
            _find = None
            _reclaimable = None
        if _find is None:
            unavailable_reason = str(
                _("Duplicate detection needs the scitex-storage package.")
            )
        else:
            ok, result = _run_bounded(
                lambda: _find(roots, max_depth=DUP_MAX_DEPTH), DUPLICATES_TIMEOUT_S
            )
            if ok:
                scanned = True
                import os

                for group in result[:50]:
                    size = 0
                    for candidate in group:
                        try:
                            size = os.lstat(candidate).st_size
                            break
                        except OSError:
                            continue
                    groups.append(
                        {
                            "paths": [str(p) for p in group[:10]],
                            "extra": max(0, len(group) - 10),
                            "count": len(group),
                            "size": size,
                        }
                    )
                try:
                    reclaimable = _reclaimable(result) if _reclaimable else None
                except OSError:
                    reclaimable = None
            else:
                err = result
                if type(err).__name__ == "MissingSystemDependencyError":
                    unavailable_reason = str(
                        _(
                            "Duplicate detection is unavailable here: "
                            "the `fclones` binary is not installed on this host. "
                            "Run `scitex-storage find-duplicates <dir>` on a host "
                            "that has it; this report stays read-only either way."
                        )
                    )
                elif isinstance(err, TimeoutError):
                    unavailable_reason = str(
                        _("Duplicate scan timed out on these volumes.")
                    )
                else:
                    unavailable_reason = str(
                        _("Duplicate scan failed: %(err)s") % {"err": err}
                    )

    context = {
        **_base_context("duplicates"),
        **scope_ctx,
        "groups": groups,
        "group_total": len(groups),
        "truncated": False,
        "reclaimable": reclaimable,
        "scanned": scanned,
        "unavailable_reason": unavailable_reason,
    }
    return render(request, "scitex_storage/organize.html", context)


def _archive_destinations() -> tuple:
    try:
        from scitex_storage._transfer._archive import DESTINATIONS
    except ImportError:
        return ()
    return tuple(DESTINATIONS)


def _tier_of(volume) -> str:
    return (getattr(volume, "tier", "") or "").strip().lower() or "other"


def move(request):
    """Tier overview + read-only archive planning. No web apply."""
    from . import volumes

    user_volumes = _user_volumes(request)
    statuses = volumes.measure_all(user_volumes)

    tiers: dict[str, list] = {}
    for st in statuses:
        tiers.setdefault(_tier_of(st.volume), []).append(st)

    destinations = _archive_destinations()
    plan = None
    plan_error = ""
    form_volume = request.GET.get("volume", "")
    form_dir = request.GET.get("dir", "")
    form_dest = request.GET.get("destination", "")
    scope_ctx = _scope_context(request, "move")
    pscope = scope_ctx["pscope"]
    if "plan" in request.GET:
        volume = volumes.find_volume(user_volumes, form_volume)
        if volume is None:
            return HttpResponseForbidden(str(_("That volume is not yours.")))
        try:
            source = volumes.contained_dir(volume, (form_dir or "").strip().strip("/"))
        except Exception:
            return HttpResponseForbidden(str(_("That folder is outside the volume.")))
        if scope_ctx["scope"] == "project":
            if pscope is None:
                plan_error = scope_ctx["scope_notice"]
            elif not _within(source, pscope.project_dir):
                return HttpResponseForbidden(
                    str(_("That folder is outside the current project."))
                )
        if plan_error:
            pass
        elif form_dest not in destinations:
            plan_error = str(
                _("Pick an archive tier: %(tiers)s.")
                % {"tiers": ", ".join(destinations) if destinations else "—"}
            )
        else:
            try:
                from scitex_storage import plan_archive as _plan
            except ImportError:
                _plan = None
            if _plan is None:
                plan_error = str(
                    _("Archive planning needs the scitex-storage package.")
                )
            else:
                ok, result = _run_bounded(
                    lambda: _plan(source, form_dest), PLAN_TIMEOUT_S
                )
                if ok:
                    plan = result
                else:
                    err = result
                    if type(err).__name__ == "MissingSystemDependencyError":
                        plan_error = str(
                            _(
                                "Archive planning is unavailable here: "
                                "the `fd` binary is not installed on this host."
                            )
                        )
                    elif isinstance(err, TimeoutError):
                        plan_error = str(_("Archive planning timed out."))
                    else:
                        plan_error = str(
                            _("Could not plan that move: %(err)s") % {"err": err}
                        )

    context = {
        **_base_context("move"),
        **scope_ctx,
        "tiers": sorted(tiers.items()),
        "reachable": volumes.REACHABLE,
        "destinations": destinations,
        "form_volume": form_volume,
        "form_dir": form_dir,
        "form_dest": form_dest,
        "user_volumes": user_volumes,
        "plan": plan,
        "plan_error": plan_error,
    }
    return render(request, "scitex_storage/organize.html", context)


_HANDLERS = {
    "usage": usage,
    "duplicates": duplicates,
    "move": move,
}


def serve(request, tab):
    """Render one organize tab. ``tab`` is a member of HANDLED_TABS."""
    return _HANDLERS[tab](request)


# EOF
