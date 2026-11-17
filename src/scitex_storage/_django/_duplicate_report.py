"""Bounded, read-only preparation of the authorized Duplicates view payload."""

from __future__ import annotations

import os
import time
from pathlib import Path


def _build_duplicate_report(
    roots, group_by, visible_path, *, depth, timeout_s, cancelled
):
    from scitex_storage import find_duplicates, group_duplicates
    from scitex_storage._measure._duplicate_identity import reclaimable_bytes

    deadline = time.monotonic() + timeout_s

    def checkpoint():
        if cancelled() or time.monotonic() >= deadline:
            raise TimeoutError("Duplicate report exceeded its scan budget")

    checkpoint()
    result = find_duplicates(roots, max_depth=depth, timeout_s=timeout_s)
    checkpoint()
    locations = {}
    if group_by:
        locations = {
            row["group_index"]: row
            for row in group_duplicates(
                result[:50],
                by=group_by,
                roots=roots,
                deadline=deadline,
                cancelled=cancelled,
            )
        }
    groups = []
    for index, group in enumerate(result[:50]):
        checkpoint()
        size = 0
        for candidate in group:
            checkpoint()
            try:
                size = os.lstat(candidate).st_size
            except OSError:
                continue
            checkpoint()
            break
        shown = group[:10]
        paths = []
        for path in shown:
            checkpoint()
            paths.append(visible_path(path))
            checkpoint()
        item = {
            "paths": paths,
            "extra": max(0, len(group) - 10),
            "count": len(group),
            "size": size,
        }
        if index in locations:
            location_group = locations[index]
            canonical_shown = set()
            for path in shown:
                checkpoint()
                canonical_shown.add(
                    str(Path(path).parent.resolve(strict=True) / Path(path).name)
                )
                checkpoint()
            displayed = []
            for location in location_group["locations"]:
                checkpoint()
                files = [path for path in location["files"] if path in canonical_shown]
                if not files:
                    continue
                label = visible_path(location["directory"])
                checkpoint()
                shown_paths = []
                for path in files:
                    checkpoint()
                    shown_paths.append(visible_path(path))
                    checkpoint()
                displayed.append({"directory": label, "paths": shown_paths})
            item.update(
                locations=displayed,
                relationship=location_group["relationship"],
                location_count=len(location_group["locations"]),
            )
        groups.append(item)
    savings = reclaimable_bytes(result, checkpoint=checkpoint)
    checkpoint()
    return {
        "groups": groups,
        "group_total": len(result),
        "truncated": len(result) > 50,
        "reclaimable": savings,
    }
