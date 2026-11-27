"""Audited deletion of a local tree the operator has declared disposable.

``purge`` is for data that has no copy to verify against and needs none: a
leftover from a completed migration, a stale cache, a superseded runtime
root. Every other deleting verb proves a copy first (``retire``) or stages
the original for a retention window (``move``, ``sweep-restorable``).
``purge`` does neither, so it compensates with guards and an audit trail:

* the caller repeats the exact absolute path (``confirm``) and states a reason;
* the path must not be ``/``, a top-level directory, a mount point, the home
  directory or an ancestor of it, and must not be a symlink;
* no mount point may sit at or under it, in any mount namespace the caller
  can read, including bind mounts whose source lies inside it;
* no process may hold it as cwd, root, executable, open file or mapping; a
  process that cannot be inspected is a refusal, not an assumed "unused";
* the walk never crosses into another filesystem;
* an audit record is written and fsynced BEFORE the first unlink. If it
  cannot be written, nothing is deleted.

The plan is computed again immediately before deleting, so a holder or
mount that appeared after the dry-run still refuses.
"""

from __future__ import annotations

import os
import shutil
import stat
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from socket import gethostname

from ._archive_transport import _manifest_dir
from ._durable import atomic_json

SCHEMA = "scitex-storage.purge-log/v1"


class PurgeRefusedError(RuntimeError):
    """A guard failed; nothing was deleted."""


@dataclass
class PurgePlan:
    path: str
    reason: str
    entries: int = 0
    apparent_bytes: int = 0
    allocated_bytes: int = 0
    expected_bytes_freed: int = 0
    hard_linked_outside: int = 0
    holders: list[str] = field(default_factory=list)
    mounts: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.refusals

    def to_dict(self) -> dict:
        return {**asdict(self), "ready": self.ready}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def purge_log_dir() -> Path:
    """Where purge audit records live (resolved per call, like manifests)."""
    return _manifest_dir().parent / "purge-logs"


def _unescape_mount(field_: str) -> str:
    # /proc/*/mountinfo escapes space, tab, newline and backslash as \ooo.
    out, i = [], 0
    while i < len(field_):
        if field_[i] == "\\" and field_[i + 1 : i + 4].isdigit():
            out.append(chr(int(field_[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field_[i])
            i += 1
    return "".join(out)


def _within(candidate: str, root: str) -> bool:
    return candidate == root or candidate.startswith(root.rstrip("/") + "/")


def _mount_point_of(path: Path) -> Path:
    current = path
    while not os.path.ismount(current):
        current = current.parent
    return current


def _find_mounts(path: Path, proc: Path) -> tuple[list[str], int]:
    """Mounts at/under ``path`` or bind-mounted from inside it, in any readable ns."""
    target = str(path)
    device = os.lstat(path).st_dev
    dev_id = f"{os.major(device)}:{os.minor(device)}"
    # Path of `path` relative to the root of its own filesystem, which is how
    # mountinfo's 4th field names the source of a bind mount.
    fs_root = _mount_point_of(path)
    inner = "/" + str(path.relative_to(fs_root)) if path != fs_root else "/"
    try:
        own_ns = os.readlink(proc / "self" / "ns" / "mnt")
    except OSError:
        own_ns = None
    found: set[str] = set()
    unreadable = 0
    seen_ns: set[str] = set()
    for table in [proc / "self" / "mountinfo", *sorted(proc.glob("[0-9]*/mountinfo"))]:
        # One table per mount namespace: every process in a namespace shares it.
        try:
            ns = os.readlink(table.parent / "ns" / "mnt")
        except FileNotFoundError:
            continue  # exited
        except PermissionError:
            unreadable += 1
            continue
        if ns in seen_ns:
            continue
        try:
            lines = table.read_text().splitlines()
        except FileNotFoundError:
            continue
        except PermissionError:
            unreadable += 1
            continue
        seen_ns.add(ns)
        where = "" if ns == own_ns else f" in {ns} (pid {table.parent.name})"
        for line in lines:
            parts = line.split()
            if len(parts) < 5:
                continue
            root, mount_point = _unescape_mount(parts[3]), _unescape_mount(parts[4])
            # Mount points are namespace-relative paths, so only ours compare.
            if ns == own_ns and _within(mount_point, target):
                found.add(f"mount point {mount_point}")
            # A bind mount names its source relative to its filesystem's root:
            # that is comparable from any namespace.
            if parts[2] == dev_id and root != "/" and _within(root, inner):
                found.add(f"bind mount of {root} at {mount_point}{where}")
    return sorted(found), unreadable


def _find_holders(path: Path, proc: Path) -> tuple[list[str], int]:
    """Processes whose cwd/root/exe/fd/mapping lies under ``path``."""
    target = str(path)
    holders: list[str] = []
    uninspectable = 0
    me = os.getpid()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            name = (entry / "comm").read_text().strip()
        except OSError:
            continue  # exited
        hits: list[str] = []
        blocked = False
        links = [entry / "cwd", entry / "root", entry / "exe"]
        try:
            links += list((entry / "fd").iterdir())
        except FileNotFoundError:
            continue
        except PermissionError:
            blocked = True
        for link in links:
            try:
                resolved = os.readlink(link)
            except FileNotFoundError:
                continue  # kernel thread (no exe) or fd closed meanwhile
            except PermissionError:
                blocked = True
                continue
            except OSError:
                continue
            resolved = resolved.removesuffix(" (deleted)")
            if _within(resolved, target):
                hits.append(f"{link.name}={resolved}")
        try:
            for line in (entry / "maps").read_text().splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) == 6 and _within(parts[5].strip(), target):
                    hits.append(f"map={parts[5].strip()}")
                    break
        except PermissionError:
            blocked = True
        except OSError:
            pass
        if hits:
            holders.append(f"pid {entry.name} ({name}): {', '.join(sorted(set(hits)))}")
        elif blocked:
            uninspectable += 1
    return holders, uninspectable


def _protected(path: Path) -> str | None:
    if path == Path("/") or len(path.parts) < 3:
        return f"refusing a top-level path: {path}"
    home = Path.home().resolve()
    if path == home or path in home.parents:
        return f"refusing the home directory or an ancestor of it: {path}"
    if os.path.ismount(path):
        return f"refusing a mount point: {path}"
    return None


def plan_purge(
    path, *, reason: str, confirm: str, proc: Path = Path("/proc")
) -> PurgePlan:
    """Inspect ``path`` and list every reason it must not be deleted. Read-only."""
    given = Path(path)
    plan = PurgePlan(path=str(given), reason=reason)
    if not given.is_absolute():
        plan.refusals.append(f"path must be absolute: {given}")
        return plan
    if confirm != str(given):
        plan.refusals.append("--confirm must repeat the exact absolute path")
    if not reason.strip():
        plan.refusals.append("a --reason is required (it is written to the audit)")
    if given.is_symlink():
        plan.refusals.append(f"path is a symlink: {given}")
        return plan
    if not given.exists():
        plan.refusals.append(f"nothing at {given}")
        return plan
    if given.resolve() != given:
        plan.refusals.append(f"path is not canonical (resolves to {given.resolve()})")
        return plan
    blocked = _protected(given)
    if blocked:
        plan.refusals.append(blocked)
        return plan

    plan.mounts, unreadable_tables = _find_mounts(given, proc)
    plan.refusals += [f"in use as {m}" for m in plan.mounts]
    plan.holders, uninspectable = _find_holders(given, proc)
    plan.refusals += [f"held by {h}" for h in plan.holders]
    if uninspectable:
        plan.refusals.append(
            f"could not inspect {uninspectable} process(es); run as a user that "
            "can read every /proc/<pid> (e.g. root) so 'unused' is measured"
        )
    if unreadable_tables:
        plan.refusals.append(f"could not read {unreadable_tables} mount table(s)")

    try:
        _measure(given, plan)
    except OSError as exc:
        plan.refusals.append(f"could not walk {given}: {exc}")
    return plan


def _measure(root: Path, plan: PurgePlan) -> None:
    """Count entries and bytes; a hard link to outside the tree frees nothing.

    Only multiply-linked non-directories are tracked by inode (directories'
    nlink counts their subdirectories, not other names), so memory stays
    proportional to the hard-linked files, not to the whole tree.
    """
    device = os.lstat(root).st_dev
    linked: dict[int, list[int]] = {}  # ino -> [allocated, nlink, seen]
    stack = [root]
    while stack:
        current = stack.pop()
        info = os.lstat(current)
        allocated = info.st_blocks * 512
        plan.entries += 1
        plan.apparent_bytes += info.st_size
        if stat.S_ISDIR(info.st_mode):
            plan.allocated_bytes += allocated
            if info.st_dev != device:
                plan.refusals.append(f"another filesystem is mounted at {current}")
                continue
            with os.scandir(current) as entries:
                stack.extend(Path(e.path) for e in entries)
        elif info.st_nlink > 1:
            linked.setdefault(info.st_ino, [allocated, info.st_nlink, 0])[2] += 1
        else:
            plan.allocated_bytes += allocated
    plan.expected_bytes_freed = plan.allocated_bytes
    for allocated, nlink, seen in linked.values():
        plan.allocated_bytes += allocated
        if seen >= nlink:
            plan.expected_bytes_freed += allocated
        else:
            plan.hard_linked_outside += 1


def _free_bytes(path: Path) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def apply_purge(plan: PurgePlan, *, proc: Path = Path("/proc")) -> dict:
    """Re-plan, write the audit, delete, and record bytes freed."""
    log_path = purge_log_dir() / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{uuid.uuid4().hex[:12]}.json"
    )
    fresh = plan_purge(plan.path, reason=plan.reason, confirm=plan.path, proc=proc)
    audit = {
        "schema": SCHEMA,
        "operation": "purge",
        "host": gethostname(),
        "uid": os.getuid(),
        "sudo_user": os.environ.get("SUDO_USER"),
        "path": plan.path,
        "reason": plan.reason,
        "plan": fresh.to_dict(),
        "started_at": _now(),
    }
    if not fresh.ready:
        audit.update(status="refused", finished_at=_now())
        _try_write(log_path, audit)
        raise PurgeRefusedError("; ".join(fresh.refusals) + " -- nothing was deleted.")

    parent = Path(plan.path).parent
    audit.update(status="deleting", free_bytes_before=_free_bytes(parent))
    try:
        atomic_json(log_path, audit)
    except OSError as exc:
        raise PurgeRefusedError(
            f"could not write the audit record {log_path}: {exc} -- nothing was deleted."
        ) from exc

    started = time.monotonic()
    try:
        shutil.rmtree(plan.path)
    except BaseException as exc:
        audit.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            remaining=os.path.lexists(plan.path),
            free_bytes_after=_free_bytes(parent),
            finished_at=_now(),
        )
        _try_write(log_path, audit)
        raise
    after = _free_bytes(parent)
    audit.update(
        status="purged",
        free_bytes_after=after,
        bytes_freed=after - audit["free_bytes_before"],
        seconds=round(time.monotonic() - started, 1),
        finished_at=_now(),
        audit_log=str(log_path),
    )
    atomic_json(log_path, audit)
    return audit


def _try_write(path: Path, value: dict) -> None:
    try:
        atomic_json(path, value)
    except OSError:
        pass


# EOF
