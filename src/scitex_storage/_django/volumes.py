#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/volumes.py
"""The storage volumes one requester may see, measured, and browsable inside.

The host decides WHICH volumes a user owns (it owns the user model and the
user-to-directory mapping); this module only measures and lists them. A host
names its provider in ``settings.SCITEX_STORAGE_VOLUMES_PROVIDER`` (dotted path
to ``provider(request) -> iterable of Volume | dict``). Standalone, with no
provider, the only volume is the process owner's home directory.

Every probe runs in a daemon thread with a deadline: ``statvfs`` on a stale
sshfs/NFS mount blocks forever, and one dead NAS must not hang the page.
"""

from __future__ import annotations

import os
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

PROBE_TIMEOUT_S = 2.0
MAX_ENTRIES = 500

REACHABLE = "reachable"
NOT_MOUNTED = "not-mounted"
UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class Volume:
    key: str
    label: str
    path: Path
    machine: str
    tier: str = ""
    connection: str = ""


@dataclass
class VolumeStatus:
    volume: Volume
    status: str
    total: int = 0
    used: int = 0
    free: int = 0
    note: str = ""

    @property
    def used_pct(self) -> Optional[float]:
        return round(100.0 * self.used / self.total, 1) if self.total else None


@dataclass
class Entry:
    name: str
    rel: str
    is_dir: bool
    size: Optional[int] = None
    mtime: Optional[float] = None


@dataclass
class Listing:
    volume: Volume
    rel: str
    entries: list = field(default_factory=list)
    truncated: bool = False
    error: str = ""

    @property
    def crumbs(self) -> list:
        parts = [p for p in self.rel.split("/") if p]
        return [
            {"name": name, "rel": "/".join(parts[: i + 1])}
            for i, name in enumerate(parts)
        ]


def with_deadline(fn: Callable, timeout: float = PROBE_TIMEOUT_S):
    """``(finished, result, exception)`` of ``fn()`` run for at most ``timeout``."""
    box: dict = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # reported to the caller, not raised in the thread
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, None, None
    return True, box.get("result"), box.get("error")


def _coerce(item) -> Volume:
    if isinstance(item, Volume):
        return item
    return Volume(
        key=str(item["key"]),
        label=str(item.get("label") or item["key"]),
        path=Path(item["path"]),
        machine=str(item.get("machine") or ""),
        tier=str(item.get("tier") or ""),
        connection=str(item.get("connection") or ""),
    )


def _provider() -> Optional[Callable]:
    from django.conf import settings
    from django.utils.module_loading import import_string

    dotted = getattr(settings, "SCITEX_STORAGE_VOLUMES_PROVIDER", "")
    return import_string(dotted) if dotted else None


def resolve_user_volumes(request) -> list:
    """The requester's volumes; empty for anyone the host does not recognise."""
    provider = _provider()
    if provider is not None:
        return [_coerce(v) for v in (provider(request) or [])]
    from django.conf import settings

    if getattr(settings, "SCITEX_APP_MODE", "standalone") != "standalone":
        return []  # hosted without a provider: fail closed
    return [
        Volume(key="home", label="Home", path=Path.home(), machine=os.uname().nodename)
    ]


def find_volume(volumes: Iterable[Volume], key: str) -> Optional[Volume]:
    return next((v for v in volumes if v.key == key), None)


def measure(volume: Volume, timeout: float = PROBE_TIMEOUT_S) -> VolumeStatus:
    def probe():
        if not volume.path.is_dir():
            return None
        return os.statvfs(volume.path)

    done, st, err = with_deadline(probe, timeout)
    if not done:
        return VolumeStatus(volume, UNREACHABLE, note="timed out")
    if err is not None:
        return VolumeStatus(volume, UNREACHABLE, note=type(err).__name__)
    if st is None:
        return VolumeStatus(volume, NOT_MOUNTED)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - st.f_bfree * st.f_frsize
    return VolumeStatus(volume, REACHABLE, total=total, used=used, free=free)


def measure_all(volumes: Iterable[Volume]) -> list:
    """Probe every volume in parallel so the page waits for one deadline, not N."""
    volumes = list(volumes)
    results: list = [None] * len(volumes)

    def work(i, v):
        results[i] = measure(v)

    threads = [threading.Thread(target=work, args=(i, v), daemon=True) for i, v in enumerate(volumes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(PROBE_TIMEOUT_S + 0.5)
    return [
        r if r is not None else VolumeStatus(v, UNREACHABLE, note="timed out")
        for r, v in zip(results, volumes)
    ]


class OutsideVolume(Exception):
    """The requested directory resolves outside the volume root."""


def contained_dir(volume: Volume, rel: str) -> Path:
    """Absolute directory for ``rel`` inside ``volume``; raises on any escape."""
    rel = (rel or "").strip().strip("/")
    if "\x00" in rel:
        raise OutsideVolume(rel)
    root = volume.path.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise OutsideVolume(rel)
    return candidate


def list_dir(volume: Volume, rel: str = "", timeout: float = PROBE_TIMEOUT_S) -> Listing:
    rel = (rel or "").strip().strip("/")
    target = contained_dir(volume, rel)
    listing = Listing(volume=volume, rel=rel)

    def scan():
        out = []
        with os.scandir(target) as it:
            for de in it:
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = stat.S_ISDIR(st.st_mode)
                out.append(
                    Entry(
                        name=de.name,
                        rel=f"{rel}/{de.name}" if rel else de.name,
                        is_dir=is_dir,
                        size=None if is_dir else st.st_size,
                        mtime=st.st_mtime,
                    )
                )
                if len(out) > MAX_ENTRIES:
                    break
        return out

    done, entries, err = with_deadline(scan, timeout)
    if not done:
        listing.error = "timeout"
    elif isinstance(err, FileNotFoundError):
        listing.error = "missing"
    elif err is not None:
        listing.error = "unreadable"
    else:
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        listing.truncated = len(entries) > MAX_ENTRIES
        listing.entries = entries[:MAX_ENTRIES]
    return listing


# EOF
