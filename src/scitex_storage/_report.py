#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Human-readable + JSON report rendering for a :class:`ScanResult`."""

from __future__ import annotations

from typing import Any

from ._scan import ScanResult

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: int | float) -> str:
    """Render a byte count as a human-readable size (e.g. ``"412.7 GB"``)."""
    size = float(num_bytes)
    for unit in _UNITS:
        if size < 1024.0 or unit == _UNITS[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"  # pragma: no cover — unreachable, satisfies mypy


def format_text_report(result: ScanResult, top: int = 20) -> str:
    """Render ``result`` as the human-readable report printed by the CLI."""
    lines: list[str] = []
    lines.append("scitex-storage scan report")
    lines.append("=" * len(lines[0]))
    lines.append(f"Root:            {result.root}")
    lines.append(
        f"Scanned:         {result.files_scanned} files, "
        f"{result.dirs_scanned} directories"
    )
    lines.append(f"Total size:      {format_size(result.total_size)}")
    if result.skipped_dirs:
        skipped = ", ".join(sorted(result.skipped_dirs))
        lines.append(
            f"Skipped:         {skipped} ({format_size(result.skipped_size)}, "
            "regenerable)"
        )

    lines.append("")
    lines.append("Top candidates (size x days-since-access):")
    candidates = result.top_candidates(top)
    if not candidates:
        lines.append("  (no files found)")
    else:
        lines.append(f"  {'score':>12}  {'size':>10}  {'last access':>12}  path")
        for e in candidates:
            score = e.score(result.scan_time)
            days = e.days_since_access(result.scan_time)
            try:
                rel = e.path.relative_to(result.root)
            except ValueError:
                rel = e.path
            lines.append(
                f"  {score:>12.0f}  {format_size(e.size):>10}  "
                f"{days:>10.0f}d  {rel}"
            )

    if result.duplicate_groups:
        lines.append("")
        lines.append("Possible duplicates (by size + hash):")
        for group in result.duplicate_groups:
            size = group[0].stat().st_size if group[0].exists() else 0
            total = format_size(size * len(group))
            lines.append(f"  {len(group)} files, {total} total:")
            for p in group:
                try:
                    rel = p.relative_to(result.root)
                except ValueError:
                    rel = p
                lines.append(f"    {rel}")

    return "\n".join(lines)


def to_json_dict(result: ScanResult, top: int = 20) -> dict[str, Any]:
    """Render ``result`` as a JSON-serializable dict (``--json`` output)."""
    candidates = result.top_candidates(top)
    return {
        "root": str(result.root),
        "files_scanned": result.files_scanned,
        "dirs_scanned": result.dirs_scanned,
        "total_size_bytes": result.total_size,
        "skipped_size_bytes": result.skipped_size,
        "skipped_dirs": sorted(result.skipped_dirs),
        "top_candidates": [
            {
                "path": str(e.path),
                "size_bytes": e.size,
                "days_since_access": round(e.days_since_access(result.scan_time), 2),
                "score": round(e.score(result.scan_time), 2),
            }
            for e in candidates
        ],
        "duplicate_groups": [
            [str(p) for p in group] for group in result.duplicate_groups
        ],
    }


# EOF
