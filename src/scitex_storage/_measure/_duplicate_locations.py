"""Read-only location breakdowns of previously confirmed duplicate groups."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path

from ._duplicate_identity import _regular_copies


def group_duplicates(
    groups: list[list[Path]],
    *,
    by: str = "parent",
    roots: list[str | Path] | None = None,
    deadline: float | None = None,
    cancelled: Callable[[], bool] | None = None,
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

    ``deadline`` is an absolute finite ``time.monotonic()`` timestamp;
    ``cancelled`` is an optional predicate such as ``threading.Event.is_set``.
    Expiry/cancellation raises TimeoutError without returning partial rows.
    Checks surround metadata operations and group boundaries. Individual
    blocking filesystem calls cannot be interrupted by these cooperative checks.
    """
    if by not in {"parent", "top-level"}:
        raise ValueError("by must be 'parent' or 'top-level'")
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise ValueError("deadline must be a finite monotonic timestamp")
        try:
            deadline = float(deadline)
        except OverflowError as exc:
            raise ValueError("deadline must be a finite monotonic timestamp") from exc
        if not math.isfinite(deadline):
            raise ValueError("deadline must be a finite monotonic timestamp")
    if cancelled is not None and not callable(cancelled):
        raise TypeError("cancelled must be a callable predicate")

    def check_progress():
        if cancelled is not None and cancelled():
            raise TimeoutError("Duplicate location grouping was cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Duplicate location grouping exceeded its deadline")

    check_progress()
    resolved = []
    if by == "top-level":
        if not roots:
            raise ValueError("top-level grouping requires directory roots")
        canonical_roots = set()
        for root in roots:
            check_progress()
            canonical_roots.add(Path(root).expanduser().resolve(strict=True))
            check_progress()
        resolved = sorted(
            canonical_roots,
            key=lambda root: (-len(root.parts), str(root)),
        )
        for root in resolved:
            check_progress()
            if not root.is_dir():
                raise NotADirectoryError(f"not a directory: {root}")
            check_progress()
    result = []
    for index, group in enumerate(groups):
        check_progress()
        copies, _ = _regular_copies(group, checkpoint=check_progress)
        if len(copies) < 2:
            continue
        locations: dict[str, list[str]] = {}
        for path, _ in copies:
            check_progress()
            canonical = path.parent.resolve(strict=True) / path.name
            check_progress()
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
    check_progress()
    return result
