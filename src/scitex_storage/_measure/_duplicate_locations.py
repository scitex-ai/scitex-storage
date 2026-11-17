"""Read-only location breakdowns of previously confirmed duplicate groups."""

from __future__ import annotations

from pathlib import Path

from ._duplicate_identity import _regular_copies


def group_duplicates(
    groups: list[list[Path]],
    *,
    by: str = "parent",
    roots: list[str | Path] | None = None,
) -> list[dict]:
    """Group confirmed duplicate files by parent or selected top-level folder.

    Content equality must already be established by ``find_duplicates``; this
    function inspects identity but does not hash or choose files to delete.
    Each result retains its zero-based input ``group_index`` and partitions its
    files into sorted ``locations`` with ``directory`` and ``files`` fields.
    ``relationship`` is ``within-location`` or ``across-locations``.

    ``parent`` uses the canonical immediate parent. ``top-level`` requires
    directory ``roots``: each file belongs to its most specific canonical root
    and is bucketed under its first child directory, or the root itself for a
    direct child file. Root argument order and directory aliases do not matter.

    Final symlinks and repeated regular-file inodes are excluded, preserving
    the duplicate finder's identity rules. Groups with fewer than two copies
    are omitted. Missing/uninspectable members raise OSError; a top-level file
    outside every selected root raises ValueError rather than being dropped.
    """
    if by not in {"parent", "top-level"}:
        raise ValueError("by must be 'parent' or 'top-level'")
    resolved = []
    if by == "top-level":
        if not roots:
            raise ValueError("top-level grouping requires directory roots")
        resolved = sorted(
            {Path(root).expanduser().resolve(strict=True) for root in roots},
            key=lambda root: (-len(root.parts), str(root)),
        )
        for root in resolved:
            if not root.is_dir():
                raise NotADirectoryError(f"not a directory: {root}")
    result = []
    for index, group in enumerate(groups):
        copies, _ = _regular_copies(group)
        if len(copies) < 2:
            continue
        locations: dict[str, list[str]] = {}
        for path, _ in copies:
            canonical = path.parent.resolve(strict=True) / path.name
            directory = canonical.parent
            if by == "top-level":
                owner = next(
                    (root for root in resolved if root in canonical.parents), None
                )
                if owner is None:
                    raise ValueError(
                        f"duplicate file is outside selected roots: {path}"
                    )
                relative = canonical.relative_to(owner)
                directory = (
                    owner if len(relative.parts) == 1 else owner / relative.parts[0]
                )
            locations.setdefault(str(directory), []).append(str(canonical))
        result.append(
            {
                "group_index": index,
                "relationship": (
                    "within-location" if len(locations) == 1 else "across-locations"
                ),
                "locations": [
                    {"directory": directory, "files": sorted(files)}
                    for directory, files in sorted(locations.items())
                ],
            }
        )
    return result
