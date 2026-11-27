#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/_tier_plan.py
"""Select the cold units of a hot volume for ``tier`` (read-only).

UNITS. A unit is either one regular file or the largest cold directory
subtree that can move as a whole. A subtree cannot move as a whole when it
holds anything that must stay behind: an excluded entry, a mount point or
another filesystem, restorable staging, an existing pointer, a special file
(FIFO, socket, device) or an unreadable entry. In that case the walk
descends and considers its children instead. The walk never crosses
filesystems and never follows symlinks (a symlink inside a subtree moves as
a symlink, the way rsync writes it).

COLDNESS. A unit is cold when the newest atime AND mtime of every file in it
are older than the cutoff. Directory atimes are ignored because listing a
directory, which this walk does, updates them. On a ``noatime`` mount reads
leave no trace, so the plan carries a warning.

ORDER. Coldest first, then largest. ``target_used_percent`` stops the
selection once projected usage reaches the target; ``max_bytes`` and
``max_items`` cap one run. Units held open by a visible process, or with a
mount at or under them, are skipped with the reason recorded.
"""

from __future__ import annotations

import os
import shutil
import stat
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._archive import (
    DEFAULT_REMOTE_ROOT,
    DESTINATIONS,
    SOURCE_KIND_DIRECTORY,
    SOURCE_KIND_FILE,
)
from ._archive_transport import _default_remote_path, resolve_destination
from ._purge import _find_holders, _find_mounts
from ._retention import STAGING_DIRNAME
from ._verify import is_excluded

POINTER_SUFFIX = ".stx-tiered.json"
DEFAULT_COLD_AFTER_DAYS = 90.0
DEFAULT_MIN_SIZE_BYTES = 100 * 1024**2
DAY_SECONDS = 86_400


def pointer_path_for(path: str | Path) -> Path:
    """Where the pointer for a tiered ``path`` lives: a sibling file."""
    path = Path(path)
    return path.parent / (path.name + POINTER_SUFFIX)


@dataclass
class TierUnit:
    """One file or directory subtree selected to move."""

    path: str
    kind: str
    size_bytes: int
    file_count: int
    newest_touch: float
    age_days: float
    remote_path: str


@dataclass
class TierPlan:
    """The result of :func:`plan_tier`. Read-only; nothing was moved."""

    root: str
    destination: str
    remote_root: str
    cold_after_days: float
    min_size_bytes: int
    exclude: tuple[str, ...]
    target_used_percent: float | None
    max_bytes: int | None
    max_items: int | None
    planned_at: float
    total_bytes: int
    used_bytes: int
    units: list[TierUnit] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uninspectable_processes: int = 0

    @property
    def selected_bytes(self) -> int:
        return sum(unit.size_bytes for unit in self.units)

    @property
    def used_percent(self) -> float | None:
        if self.total_bytes <= 0:
            return None
        return 100.0 * self.used_bytes / self.total_bytes

    @property
    def projected_used_percent(self) -> float | None:
        """Usage once every selected unit's restorable staging is swept."""
        if self.total_bytes <= 0:
            return None
        projected = max(self.used_bytes - self.selected_bytes, 0)
        return 100.0 * projected / self.total_bytes

    def to_dict(self) -> dict:
        data = asdict(self)
        data["exclude"] = list(self.exclude)
        data["selected_bytes"] = self.selected_bytes
        data["used_percent"] = self.used_percent
        data["projected_used_percent"] = self.projected_used_percent
        return data


@dataclass
class _Node:
    path: str
    kind: str
    size: int = 0
    count: int = 0
    newest: float = 0.0
    blocker: str = ""
    children: list = field(default_factory=list)


def _stays_reason(name: str, rel: str, exclude: tuple[str, ...]) -> str:
    """Why an entry is never moved by tier ('' when it may move)."""
    if exclude and is_excluded(rel, exclude):
        return "excluded"
    if name == STAGING_DIRNAME:
        return "restorable staging"
    if name.endswith(POINTER_SUFFIX):
        return "tier pointer"
    return ""


def _add(node: _Node, size: int, count: int, newest: float) -> None:
    node.size += size
    node.count += count
    node.newest = max(node.newest, newest)


def _block(node: _Node, ctx: dict, path: str, reason: str) -> None:
    node.blocker = node.blocker or f"holds an entry that stays ({reason})"
    ctx["skipped"].append({"path": path, "reason": reason})


def walk_tree(path: str, rel: str, device: int, ctx: dict) -> _Node:
    """Aggregate size, count and newest touch of a directory, post-order.

    ``ctx`` carries ``exclude``, ``cutoff``, ``min_size`` and the shared
    ``skipped`` list. Only directories and files of at least ``min_size``
    are kept as children; smaller files are aggregated only.
    """
    node = _Node(path, SOURCE_KIND_DIRECTORY)
    try:
        node.newest = os.lstat(path).st_mtime
        entries = list(os.scandir(path))
    except OSError as exc:
        node.blocker = f"unreadable: {exc}"
        ctx["skipped"].append({"path": path, "reason": node.blocker})
        return node
    for entry in entries:
        child_rel = f"{rel}/{entry.name}" if rel else entry.name
        reason = _stays_reason(entry.name, child_rel, ctx["exclude"])
        if reason:
            _block(node, ctx, entry.path, reason)
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            _block(node, ctx, entry.path, f"unreadable: {exc}")
            continue
        if info.st_dev != device:
            _block(node, ctx, entry.path, "another filesystem (not crossed)")
        elif stat.S_ISDIR(info.st_mode):
            child = walk_tree(entry.path, child_rel, device, ctx)
            _add(node, child.size, child.count, child.newest)
            if child.blocker:
                node.blocker = node.blocker or "holds an entry that stays"
            elif child.newest < ctx["cutoff"] and child.size >= ctx["min_size"]:
                child.children = []  # taken whole or not at all
            node.children.append(child)
        elif stat.S_ISREG(info.st_mode):
            touch = max(info.st_atime, info.st_mtime)
            _add(node, info.st_size, 1, touch)
            if info.st_size >= ctx["min_size"]:
                linked = "hard-linked (moving it frees nothing)"
                node.children.append(
                    _Node(
                        entry.path,
                        SOURCE_KIND_FILE,
                        info.st_size,
                        1,
                        touch,
                        linked if info.st_nlink > 1 else "",
                    )
                )
        elif stat.S_ISLNK(info.st_mode):
            _add(node, info.st_size, 1, info.st_mtime)
        else:
            _block(node, ctx, entry.path, "special file")
    return node


def _collect(node: _Node, ctx: dict, out: list[_Node]) -> None:
    """Pick the largest cold units top-down; descend where a whole cannot move."""
    for child in node.children:
        cold = child.newest < ctx["cutoff"]
        if child.kind == SOURCE_KIND_FILE:
            if cold and child.blocker:
                ctx["skipped"].append({"path": child.path, "reason": child.blocker})
            elif cold:
                out.append(child)
        elif not child.blocker and cold and child.size >= ctx["min_size"]:
            if child.count:
                out.append(child)
        else:
            _collect(child, ctx, out)


def _atime_warnings(root: Path) -> list[str]:
    """Warn when reads on ROOT's filesystem leave no atime."""
    try:
        lines = Path("/proc/self/mounts").read_text().splitlines()
    except OSError as exc:
        return [f"could not read /proc/self/mounts to check atime mode: {exc}"]
    best, options = "", ""
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        mount = parts[1].replace("\\040", " ")
        inside = str(root) == mount or str(root).startswith(mount.rstrip("/") + "/")
        if inside and len(mount) >= len(best):
            best, options = mount, parts[3]
    if "noatime" in options.split(","):
        return [
            f"{best} is mounted noatime: reads leave no atime, so coldness here "
            "is measured by modification time alone"
        ]
    return []


def holder_reason(path: str, proc: Path) -> tuple[str, int]:
    """Why ``path`` must not move right now ('' when nothing was found).

    Reuses purge's /proc scans: any visible process holding a cwd, root,
    exe, open file or mapping under ``path``, or any mount at or under it.
    Returns the reason and the number of processes that could not be
    inspected (another user's), which the plan reports as a coverage gap.
    """
    holders, uninspectable = _find_holders(Path(path), proc)
    if holders:
        return f"held open: {holders[0]}", uninspectable
    mounts, _unreadable = _find_mounts(Path(path), proc)
    if mounts:
        return f"in use as {mounts[0]}", uninspectable
    return "", uninspectable


def _stop_reason(plan: TierPlan, projected: int) -> str:
    if plan.max_items is not None and len(plan.units) >= plan.max_items:
        return "--max-items reached"
    target = plan.target_used_percent
    if target is not None and plan.total_bytes > 0:
        if 100.0 * projected / plan.total_bytes <= target:
            return "target usage reached"
    return ""


def plan_tier(
    root: str | Path,
    *,
    destination: str,
    remote_root: str | None = None,
    cold_after_days: float = DEFAULT_COLD_AFTER_DAYS,
    min_size_bytes: int = DEFAULT_MIN_SIZE_BYTES,
    exclude: tuple[str, ...] = (),
    target_used_percent: float | None = None,
    max_bytes: int | None = None,
    max_items: int | None = None,
    disk_usage=shutil.disk_usage,
    proc: Path = Path("/proc"),
    now: float | None = None,
) -> TierPlan:
    """Select cold units under ``root``, coldest first, then largest.

    Read-only. ``target_used_percent`` stops the selection once the
    filesystem's projected usage (current usage minus the selected bytes)
    reaches the target; nothing is selected when usage is already at or below
    it. ``disk_usage``, ``proc`` and ``now`` are seams for tests.
    """
    destination, _notice = resolve_destination(destination)
    if destination not in DESTINATIONS:
        raise ValueError(
            f"destination must be one of {DESTINATIONS}, got {destination!r}"
        )
    if cold_after_days < 0 or min_size_bytes < 0:
        raise ValueError("cold_after_days and min_size_bytes must be nonnegative")
    if target_used_percent is not None and not 0 <= target_used_percent <= 100:
        raise ValueError("target_used_percent must be between 0 and 100")
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise NotADirectoryError(f"not a directory: {root_path}")
    root_path = root_path.resolve()
    moment = time.time() if now is None else now
    usage = disk_usage(str(root_path))
    plan = TierPlan(
        root=str(root_path),
        destination=destination,
        remote_root=remote_root or DEFAULT_REMOTE_ROOT,
        cold_after_days=float(cold_after_days),
        min_size_bytes=int(min_size_bytes),
        exclude=tuple(exclude),
        target_used_percent=target_used_percent,
        max_bytes=max_bytes,
        max_items=max_items,
        planned_at=moment,
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        warnings=_atime_warnings(root_path),
    )
    ctx = {
        "exclude": plan.exclude,
        "cutoff": moment - cold_after_days * DAY_SECONDS,
        "min_size": plan.min_size_bytes,
        "skipped": plan.skipped,
    }
    tree = walk_tree(str(root_path), "", os.lstat(root_path).st_dev, ctx)
    candidates: list[_Node] = []
    _collect(tree, ctx, candidates)
    candidates.sort(key=lambda node: (node.newest, -node.size))

    projected = plan.used_bytes
    for index, node in enumerate(candidates):
        stop = _stop_reason(plan, projected)
        if stop:
            plan.skipped += [
                {"path": n.path, "reason": stop} for n in candidates[index:]
            ]
            break
        if max_bytes is not None and plan.selected_bytes + node.size > max_bytes:
            plan.skipped.append(
                {"path": node.path, "reason": "would exceed --max-bytes"}
            )
            continue
        if pointer_path_for(node.path).exists():
            plan.skipped.append({"path": node.path, "reason": "a tier pointer exists"})
            continue
        reason, uninspectable = holder_reason(node.path, proc)
        plan.uninspectable_processes = max(plan.uninspectable_processes, uninspectable)
        if reason:
            plan.skipped.append({"path": node.path, "reason": reason})
            continue
        plan.units.append(
            TierUnit(
                path=node.path,
                kind=node.kind,
                size_bytes=node.size,
                file_count=node.count,
                newest_touch=node.newest,
                age_days=(moment - node.newest) / DAY_SECONDS,
                remote_path=_default_remote_path(Path(node.path), plan.remote_root),
            )
        )
        projected -= node.size
    if plan.uninspectable_processes:
        plan.warnings.append(
            f"{plan.uninspectable_processes} process(es) could not be inspected "
            "for open files; only holders visible to this user were checked"
        )
    return plan


# EOF
