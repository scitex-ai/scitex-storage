"""Validate duplicate-engine report structure before inspecting any paths."""

from __future__ import annotations

import re

_ANSI_COLOR = re.compile(r"\x1b\[[0-9;]*m")
_INFO_LINE = re.compile(r"^\[[^\]\r\n]+\]\s+fclones:\s+info:")


def _check_engine_diagnostics(stderr: bytes) -> None:
    """Only known informational logs may accompany a successful engine report.

    fclones 0.35 can exit zero after unreadable directories or failed file reads.
    Its warning is not encoded in the groups array (or missing-file count).
    Unknown/non-informational diagnostics therefore refuse a complete verdict.
    """
    lines = [
        _ANSI_COLOR.sub("", line).strip()
        for line in stderr.decode("utf-8", "replace").splitlines()
    ]
    diagnostics = [line for line in lines if line and not _INFO_LINE.match(line)]
    if diagnostics:
        detail = "; ".join(line[:1000] for line in diagnostics[:3])
        if len(diagnostics) > 3:
            detail += f" (+{len(diagnostics) - 3} more diagnostic lines)"
        raise RuntimeError(
            "Incomplete fclones scan: engine reported non-informational "
            f"diagnostics; completeness cannot be confirmed: {detail}"
        )


def _reported_groups(payload: object) -> list[list[str]]:
    """Reject incomplete or malformed reports instead of reporting a clean scan."""
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise RuntimeError("Invalid fclones JSON report: expected a groups array")
    header = payload.get("header")
    if header is not None:
        if not isinstance(header, dict):
            raise RuntimeError("Invalid fclones JSON report: expected a header object")
        stats = header.get("stats")
        if stats is not None:
            if not isinstance(stats, dict):
                raise RuntimeError(
                    "Invalid fclones JSON report: expected a stats object"
                )
            missing = stats.get("missing_file_count")
            if "missing_file_count" in stats:
                if (
                    isinstance(missing, bool)
                    or not isinstance(missing, int)
                    or missing < 0
                ):
                    raise RuntimeError(
                        "Invalid fclones JSON report: invalid missing-file count"
                    )
                if missing:
                    raise RuntimeError(
                        "Incomplete fclones scan: engine reported "
                        f"{missing} missing files"
                    )
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
