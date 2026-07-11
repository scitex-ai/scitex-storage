#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable + JSON rendering for scan and prune results."""

from __future__ import annotations

from typing import Any

from ._archive import ArchiveManifest, ArchivePlan, RestorePlan
from ._images import ApplyResult, PrunePlan
from ._scan import RootScan
from ._sweep import SweepPlan, SweepResult, SweptEntry

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: int | float) -> str:
    """Render a byte count as a human-readable size (e.g. ``"412.7 GB"``)."""
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024.0 or unit == _UNITS[-1]:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"  # pragma: no cover — unreachable, satisfies mypy


def format_count(num: int) -> str:
    """Render an inode / file count with thousands separators (e.g. ``"1,234"``)."""
    return f"{num:,}"


def format_root_report(result: RootScan, top: int = 20, sort: str = "size") -> str:
    """Render one :class:`RootScan` as the human-readable table the CLI prints."""
    ordered = result.by_file_count() if sort == "files" else result.by_size()
    n = len(result.children)

    lines: list[str] = []
    header = f"scitex-storage scan  {result.root}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"{format_size(result.total_size)} in "
        f"{format_count(result.total_files)} files across "
        f"{n} top-level {'child' if n == 1 else 'children'}  "
        f"(sorted by {sort})"
    )
    lines.append("")

    if not ordered:
        lines.append("  (empty)")
        return "\n".join(lines)

    lines.append(f"  {'SIZE':>10}  {'FILES':>10}  CHILD")
    lines.append(f"  {'-' * 10}  {'-' * 10}  {'-' * 24}")
    for c in ordered[:top]:
        name = c.name + ("/" if c.is_dir else "")
        note = f"  [{c.error}]" if c.error else ""
        lines.append(
            f"  {format_size(c.size):>10}  {format_count(c.file_count):>10}  "
            f"{name}{note}"
        )
    if n > top:
        lines.append(f"  {'':>10}  {'':>10}  ... and {n - top} more")
    lines.append(f"  {'-' * 10}  {'-' * 10}  {'-' * 24}")
    lines.append(
        f"  {format_size(result.total_size):>10}  "
        f"{format_count(result.total_files):>10}  TOTAL"
    )
    return "\n".join(lines)


def format_report(
    results: list[RootScan], top: int = 20, sort: str = "size"
) -> str:
    """Render several roots' reports, separated by a blank line."""
    return "\n\n".join(
        format_root_report(r, top=top, sort=sort) for r in results
    )


def _child_dict(c: Any) -> dict[str, Any]:
    return {
        "name": c.name,
        "path": str(c.path),
        "size_bytes": c.size,
        "file_count": c.file_count,
        "is_dir": c.is_dir,
        "newest_mtime": c.newest_mtime,
        "error": c.error,
    }


def to_json_dict(
    results: list[RootScan], top: int = 20, sort: str = "size"
) -> dict[str, Any]:
    """Render several roots' scans as a JSON-serializable dict (``--json``)."""
    roots = []
    for result in results:
        ordered = result.by_file_count() if sort == "files" else result.by_size()
        roots.append(
            {
                "root": str(result.root),
                "total_size_bytes": result.total_size,
                "total_files": result.total_files,
                "children": [_child_dict(c) for c in ordered[:top]],
            }
        )
    return {"roots": roots}


def format_prune_report(
    plan: PrunePlan, applied: bool, apply_result: ApplyResult | None = None
) -> str:
    """Render a :class:`~scitex_storage._images.PrunePlan` as the CLI text.

    ``apply_result`` is required when ``applied`` is true — it carries what
    actually happened (some ``plan.remove`` candidates may have been
    skipped as in-use rather than unlinked).
    """
    lines: list[str] = []
    header = f"scitex-storage images prune  {plan.directory}"
    lines.append(header)
    lines.append("=" * len(header))
    kept_unreferenced = len(plan.kept) - len(plan.referenced)
    lines.append(
        f"{len(plan.referenced)} referenced (always kept), "
        f"{kept_unreferenced} newest kept, "
        f"{len(plan.remove)} to remove"
    )
    lines.append("")

    if plan.remove:
        verb = "REMOVED" if applied else "WOULD REMOVE"
        removed_paths = (
            {c.path for c in apply_result.removed} if applied and apply_result else None
        )
        lines.append(f"  {verb}:")
        for c in plan.remove:
            if removed_paths is not None and c.path not in removed_paths:
                continue
            lines.append(f"    {format_size(c.size):>10}  {c.path.name}")
    else:
        lines.append("  (nothing to remove)")

    if applied and apply_result and apply_result.skipped_in_use:
        lines.append("")
        lines.append("  SKIPPED (in use):")
        for s in apply_result.skipped_in_use:
            pids = ", ".join(str(p) for p in s.pids)
            lines.append(
                f"    {format_size(s.candidate.size):>10}  "
                f"{s.candidate.path.name}  [open by pid {pids}]"
            )

    reclaimed = (
        apply_result.reclaimed_bytes if applied and apply_result else plan.reclaimable_bytes
    )
    lines.append("")
    lines.append(f"  {format_size(reclaimed)} {'reclaimed' if applied else 'reclaimable'}")
    if not applied and plan.remove:
        lines.append("  (dry-run — pass --apply to actually delete)")
    return "\n".join(lines)


def _prune_candidate_dict(c: Any) -> dict[str, Any]:
    return {"path": str(c.path), "size_bytes": c.size, "mtime": c.mtime}


def prune_plan_to_json_dict(
    plan: PrunePlan, applied: bool, apply_result: ApplyResult | None = None
) -> dict[str, Any]:
    """Render a :class:`~scitex_storage._images.PrunePlan` as a JSON dict."""
    payload: dict[str, Any] = {
        "directory": str(plan.directory),
        "applied": applied,
        "referenced": [_prune_candidate_dict(c) for c in plan.referenced],
        "kept": [_prune_candidate_dict(c) for c in plan.kept],
        "remove": [_prune_candidate_dict(c) for c in plan.remove],
        "reclaimable_bytes": plan.reclaimable_bytes,
    }
    if applied and apply_result:
        payload["removed"] = [_prune_candidate_dict(c) for c in apply_result.removed]
        payload["skipped_in_use"] = [
            {**_prune_candidate_dict(s.candidate), "pids": s.pids}
            for s in apply_result.skipped_in_use
        ]
        payload["reclaimed_bytes"] = apply_result.reclaimed_bytes
    return payload


def format_sweep_report(
    plan: SweepPlan, applied: bool, result: SweepResult | None = None
) -> str:
    """Render a :class:`~scitex_storage._sweep.SweepPlan` as the CLI text.

    ``result`` is required when ``applied`` is true.
    """
    lines: list[str] = []
    header = f"scitex-storage sweep  {plan.directory}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"threshold >= {format_count(plan.threshold_files)} files, "
        f"min age {plan.min_age_seconds / 3600:.0f}h  "
        f"({len(plan.candidates)} candidate(s), "
        f"{len(plan.skipped_fresh)} skipped as too fresh)"
    )
    lines.append("")

    if not plan.candidates:
        lines.append("  (no candidates)")
    elif applied and result:
        swept_names = {s.candidate.name for s in result.swept}
        stopped_names = {c.name for c in result.stopped_low_walltime}
        lines.append("  SWEPT:")
        for s in result.swept:
            lines.append(
                f"    {format_count(s.member_count):>10}  {s.candidate.name}"
                f"  -> {s.tar_path.name} ({format_size(s.tar_size)})"
            )
        untouched = [
            c for c in plan.candidates if c.name not in swept_names | stopped_names
        ]
        if result.stopped_low_walltime:
            lines.append("")
            lines.append("  STOPPED (low remaining walltime):")
            for c in result.stopped_low_walltime:
                lines.append(f"    {format_count(c.file_count):>10}  {c.name}")
        if untouched:
            lines.append("")
            lines.append("  NOT CONFIRMED (left untouched):")
            for c in untouched:
                lines.append(f"    {format_count(c.file_count):>10}  {c.name}")
    else:
        lines.append("  CANDIDATES (dry-run):")
        for c in plan.candidates:
            lines.append(
                f"    {format_count(c.file_count):>10}  {c.name}"
                f"  (~{format_count(max(0, c.file_count - 1))} inodes reclaimable)"
            )

    if plan.skipped_fresh:
        lines.append("")
        lines.append("  SKIPPED (too fresh, possibly still in use):")
        for c in plan.skipped_fresh:
            lines.append(f"    {format_count(c.file_count):>10}  {c.name}")

    reclaimed = result.reclaimed_inodes if applied and result else plan.reclaimable_inodes
    lines.append("")
    lines.append(
        f"  {format_count(reclaimed)} inodes "
        f"{'reclaimed' if applied else 'reclaimable'}"
    )
    if not applied and plan.candidates:
        lines.append(
            "  (dry-run — pass --apply --confirm NAME [--confirm NAME ...] to sweep)"
        )
    return "\n".join(lines)


def _sweep_candidate_dict(c: Any) -> dict[str, Any]:
    return {
        "name": c.name,
        "path": str(c.path),
        "file_count": c.file_count,
        "size_bytes": c.size,
        "newest_mtime": c.newest_mtime,
    }


def sweep_plan_to_json_dict(
    plan: SweepPlan, applied: bool, result: SweepResult | None = None
) -> dict[str, Any]:
    """Render a :class:`~scitex_storage._sweep.SweepPlan` as a JSON dict."""
    payload: dict[str, Any] = {
        "directory": str(plan.directory),
        "threshold_files": plan.threshold_files,
        "min_age_seconds": plan.min_age_seconds,
        "applied": applied,
        "candidates": [_sweep_candidate_dict(c) for c in plan.candidates],
        "skipped_fresh": [_sweep_candidate_dict(c) for c in plan.skipped_fresh],
        "reclaimable_inodes": plan.reclaimable_inodes,
    }
    if applied and result:
        payload["swept"] = [
            {
                **_sweep_candidate_dict(s.candidate),
                "tar_path": str(s.tar_path),
                "tar_size_bytes": s.tar_size,
                "member_count": s.member_count,
                "reclaimed_inodes": s.reclaimed_inodes,
            }
            for s in result.swept
        ]
        payload["stopped_low_walltime"] = [
            _sweep_candidate_dict(c) for c in result.stopped_low_walltime
        ]
        payload["reclaimed_inodes"] = result.reclaimed_inodes
    return payload


def format_sweep_status_report(directory: str, entries: list[SweptEntry]) -> str:
    """Render :func:`~scitex_storage._sweep.sweep_status` results as CLI text."""
    lines: list[str] = []
    header = f"scitex-storage sweep-status  {directory}"
    lines.append(header)
    lines.append("=" * len(header))
    if not entries:
        lines.append("  (nothing swept)")
        return "\n".join(lines)
    for e in entries:
        note = "  [ANOMALY: original directory still present]" if e.original_still_present else ""
        lines.append(f"  {format_size(e.tar_size):>10}  {e.name}.tar{note}")
    return "\n".join(lines)


def sweep_status_to_json_dict(
    directory: str, entries: list[SweptEntry]
) -> dict[str, Any]:
    """Render :func:`~scitex_storage._sweep.sweep_status` results as a JSON dict."""
    return {
        "directory": directory,
        "swept": [
            {
                "name": e.name,
                "tar_path": str(e.tar_path),
                "tar_size_bytes": e.tar_size,
                "original_still_present": e.original_still_present,
            }
            for e in entries
        ],
    }


def format_archive_report(
    plan: ArchivePlan, applied: bool, manifest: ArchiveManifest | None = None
) -> str:
    """Render an :class:`~scitex_storage._archive.ArchivePlan` as CLI text."""
    lines: list[str] = []
    header = f"scitex-storage archive  {plan.source}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"{format_size(plan.size_bytes)} in {format_count(plan.file_count)} files "
        f"-> {plan.destination}:{plan.remote_path}"
    )
    lines.append("")
    if applied and manifest:
        lines.append(
            f"  ARCHIVED (checksummed={manifest.checksummed}) -- "
            f"source removed, manifest written to {plan.manifest_path}"
        )
    else:
        lines.append("  WOULD ARCHIVE (dry-run — pass --apply to actually sync + remove)")
    return "\n".join(lines)


def archive_plan_to_json_dict(
    plan: ArchivePlan, applied: bool, manifest: ArchiveManifest | None = None
) -> dict[str, Any]:
    """Render an :class:`~scitex_storage._archive.ArchivePlan` as a JSON dict."""
    payload: dict[str, Any] = {
        "source": str(plan.source),
        "destination": plan.destination,
        "remote_path": plan.remote_path,
        "size_bytes": plan.size_bytes,
        "file_count": plan.file_count,
        "manifest_path": str(plan.manifest_path),
        "applied": applied,
    }
    if applied and manifest:
        payload["manifest"] = manifest.to_dict()
    return payload


def format_restore_report(
    plan: RestorePlan, applied: bool, restored_path: Any | None = None
) -> str:
    """Render a :class:`~scitex_storage._archive.RestorePlan` as CLI text."""
    m = plan.manifest
    lines: list[str] = []
    header = f"scitex-storage restore  {m.source}"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"{format_size(m.size_bytes)} in {format_count(m.file_count)} files "
        f"<- {m.destination}:{m.remote_path}  (archived {m.archived_at:.0f})"
    )
    lines.append("")
    if applied and restored_path:
        lines.append(f"  RESTORED to {restored_path}")
    else:
        lines.append("  WOULD RESTORE (dry-run — pass --apply to actually pull)")
    return "\n".join(lines)


def restore_plan_to_json_dict(
    plan: RestorePlan, applied: bool, restored_path: Any | None = None
) -> dict[str, Any]:
    """Render a :class:`~scitex_storage._archive.RestorePlan` as a JSON dict."""
    payload: dict[str, Any] = {
        "manifest": plan.manifest.to_dict(),
        "manifest_path": str(plan.manifest_path),
        "applied": applied,
    }
    if applied and restored_path:
        payload["restored_path"] = str(restored_path)
    return payload


# EOF
