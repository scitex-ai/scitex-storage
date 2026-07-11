#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable + JSON rendering for :class:`~scitex_storage._scan.RootScan`."""

from __future__ import annotations

from typing import Any

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


# EOF
