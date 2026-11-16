"""Validate duplicate-engine report structure before inspecting any paths."""

from __future__ import annotations


def _reported_groups(payload: object) -> list[list[str]]:
    """Reject incomplete or malformed reports instead of reporting a clean scan."""
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise RuntimeError("Invalid fclones JSON report: expected a groups array")
    groups = []
    for group in payload["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("files"), list):
            raise RuntimeError("Invalid fclones JSON report: expected a files array")
        paths = group["files"]
        if any(not isinstance(path, str) or not path or "\0" in path for path in paths):
            raise RuntimeError(
                "Invalid fclones JSON report: expected nonempty file paths"
            )
        groups.append(paths)
    return groups
