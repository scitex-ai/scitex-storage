#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable + JSON rendering for scan and prune results."""

from __future__ import annotations

from typing import Any

from ._images import ApplyResult, PrunePlan
from ._scan import RootScan

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


# EOF
