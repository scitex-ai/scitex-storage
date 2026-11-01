"""Verified local moves between caller-authorized roots, with tracked recovery.

This layer knows paths, not users, tiers, hosts, or prices. A web host must
validate both roots and supply a trusted per-user state directory. No process
HOME or global project selection is changed for a request.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ._content_verify import DigestCancelled
from ._durable import atomic_json, file_lock, rename_noreplace, sync_directory
from ._retention import _snapshot, stage_for_retention


@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    transaction_id: str
    manifest_path: Path
    size_bytes: int
    entry_count: int
    source_identity: tuple[int, int]
    parent_identity: tuple[int, int]


@dataclass
class MoveManifest:
    transaction_id: str
    source: str
    destination: str
    manifest_path: str
    size_bytes: int
    started_at: float
    phase: str = "accepted"
    verification_method: str = "pending"
    content_digest: str = ""
    temporary_path: str = ""
    destination_published: bool = False
    staged_path: str = ""
    finished_at: float = 0.0
    process_pid: int | None = None
    error: str = ""


class MoveRecoveryRequired(RuntimeError):
    """An existing or partially completed transaction needs explicit recovery."""

    def __init__(self, message, manifest):
        super().__init__(message)
        self.manifest = manifest


class MoveInProgress(MoveRecoveryRequired):
    """Another worker owns this transaction; do not replay it."""


class MoveCancelled(MoveRecoveryRequired):
    """The move stopped; any published verified copy is retained."""


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _state_dir(state_dir):
    root = (
        Path(state_dir)
        if state_dir is not None
        else Path.home() / ".scitex/scitex-storage/runtime/move-manifests"
    )
    return root.expanduser().resolve()


def _transaction_id(value):
    return uuid.uuid4().hex if value is None else uuid.UUID(str(value)).hex


def _estimate(source, max_entries):
    if source.is_file():
        return source.stat().st_size, 1
    total = 0
    count = 0

    def failed(error):
        raise error

    for directory, dirs, files in os.walk(source, followlinks=False, onerror=failed):
        count += len(dirs) + len(files)
        if count > max_entries:
            raise ValueError("Move planning entry budget exceeded")
        for name in [
            *files,
            *(name for name in dirs if (Path(directory) / name).is_symlink()),
        ]:
            total += (Path(directory) / name).lstat().st_size
    return total, count


def plan_move(
    source, destination, *, state_dir=None, transaction_id=None, max_entries=100000
) -> MovePlan:
    """Plan without writing or hashing content; never overwrite a destination.

    Source/destination are filesystem paths authorized by the caller. ``state_dir``
    must be host-supplied for a GUI, never a browser-provided absolute path.
    Planning refuses large inventories beyond its explicit metadata entry budget.
    """
    raw = Path(source).expanduser()
    if raw.is_symlink():
        raise ValueError("A move root cannot be a symbolic link")
    source = raw.resolve(strict=True)
    if raw.absolute() != source:
        raise ValueError("Move source must be a caller-authorized canonical path")
    if source == source.parent or not (source.is_dir() or source.is_file()):
        raise ValueError(
            "Move source must be a regular file or directory, not a filesystem root"
        )
    requested = Path(destination).expanduser().absolute()
    parent = requested.parent.resolve(strict=True)
    if parent != requested.parent:
        raise ValueError("Move destination parent must be canonical")
    destination = parent / requested.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(str(destination))
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Source and destination cannot contain one another")
    record_root = _state_dir(state_dir)
    if any(
        path.is_relative_to(record_root) or record_root.is_relative_to(path)
        for path in (source, destination)
    ):
        raise ValueError("Move paths cannot overlap the transaction state directory")
    if max_entries < 1:
        raise ValueError("Planning entry budget must be positive")
    size, count = _estimate(source, max_entries)
    identifier = _transaction_id(transaction_id)
    return MovePlan(
        source,
        destination,
        identifier,
        record_root / f"{identifier}.json",
        size,
        count,
        _identity(source),
        _identity(destination.parent),
    )


def get_move_status(transaction_id, *, state_dir=None) -> MoveManifest:
    """Read a historical receipt, not a worker-liveness assertion.

    The host must authorize the state directory. Active ownership is established
    by the live transaction lock; a saved phase alone does not prove a worker is
    still running after a host restart.
    """
    identifier = _transaction_id(transaction_id)
    path = _state_dir(state_dir) / f"{identifier}.json"
    record = MoveManifest(**json.loads(path.read_text(encoding="utf-8")))
    if record.transaction_id != identifier or Path(record.manifest_path) != path:
        raise ValueError("Transaction identity does not match its state file")
    return record


class _TerminationPending(RuntimeError):
    """The OS has not yet acknowledged termination of the owned group."""


def _terminate_group(process):
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Process group termination is still pending in the OS"
        ) from exc


def _run_process_group(command, stopped, on_start=None):
    """Run a real process in its own group; cancellation kills all descendants."""
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command, stdout=output, stderr=output, start_new_session=True
        )
        try:
            if on_start is not None:
                on_start(process.pid)
            while process.poll() is None:
                if stopped():
                    _terminate_group(process)
                    raise DigestCancelled("Move cancelled or deadline expired")
                try:
                    process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                output.seek(0, os.SEEK_END)
                output.seek(max(0, output.tell() - 4096))
                detail = output.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Transfer process exited {process.returncode}: {detail}"
                )
        finally:
            if process.poll() is None:
                _terminate_group(process)


def _existing(plan):
    if not plan.manifest_path.exists():
        return None
    record = get_move_status(plan.transaction_id, state_dir=plan.manifest_path.parent)
    if (record.source, record.destination) != (str(plan.source), str(plan.destination)):
        raise MoveRecoveryRequired("Transaction ID belongs to a different plan", record)
    if record.phase == "retired":
        return record
    raise MoveRecoveryRequired(
        "Existing transaction must not be blindly replayed", record
    )


def apply_move(
    plan: MovePlan, *, timeout_s=120.0, deadline=None, cancel_event=None
) -> MoveManifest:
    """Copy privately, verify independently, publish atomically, stage for 7 days.

    Reusing a completed transaction returns its historical receipt. Other
    existing phases require explicit recovery. A live lock returns MoveInProgress.
    Deadlines are monotonic; hashes cooperate between reads and rsync runs in a
    cancellable process group. No job or periodic cleanup is detached here.
    """
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Move timeout must be positive and finite")
    until = time.monotonic() + timeout_s
    if deadline is not None:
        if not math.isfinite(deadline):
            raise ValueError("Move deadline must be finite")
        until = min(until, deadline)

    def stopped():
        return time.monotonic() >= until or bool(cancel_event and cancel_event.is_set())

    try:
        with file_lock(plan.manifest_path.with_suffix(".lock"), blocking=False):
            previous = _existing(plan)
            if previous is not None:
                return previous
            return _apply(plan, stopped)
    except BlockingIOError as exc:
        try:
            record = get_move_status(
                plan.transaction_id, state_dir=plan.manifest_path.parent
            )
        except FileNotFoundError:
            record = MoveManifest(
                plan.transaction_id,
                str(plan.source),
                str(plan.destination),
                str(plan.manifest_path),
                plan.size_bytes,
                time.time(),
            )
        raise MoveInProgress(
            "Transaction is already owned by a worker", record
        ) from exc


def _apply(plan, stopped):
    record = MoveManifest(
        plan.transaction_id,
        str(plan.source),
        str(plan.destination),
        str(plan.manifest_path),
        plan.size_bytes,
        time.time(),
    )

    def save(phase):
        record.phase = phase
        atomic_json(plan.manifest_path, asdict(record))

    temporary = None
    save("accepted")
    try:
        if stopped():
            raise DigestCancelled("Move cancelled before transfer")
        if (
            plan.source.resolve(strict=True) != plan.source
            or _identity(plan.source) != plan.source_identity
        ):
            raise ValueError("Source identity changed since planning")
        if (
            plan.destination.parent.resolve() != plan.destination.parent
            or _identity(plan.destination.parent) != plan.parent_identity
        ):
            raise ValueError("Destination parent changed since planning")
        if plan.destination.exists() or plan.destination.is_symlink():
            raise FileExistsError(str(plan.destination))
        baseline, size = _snapshot(plan.source, cancelled=stopped)
        if size != plan.size_bytes:
            raise ValueError("Source size changed since planning; re-plan")
        if shutil.disk_usage(plan.destination.parent).free < size:
            raise OSError("Destination has insufficient free space")
        binary = shutil.which("rsync")
        if binary is None:
            raise FileNotFoundError("Local moves require the rsync executable")
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f".stx-move-{plan.transaction_id}-", dir=plan.destination.parent
            )
        )
        payload = temporary / "payload"
        source_arg = str(plan.source)
        if plan.source.is_dir():
            payload.mkdir()
            source_arg += "/"
        record.temporary_path = str(temporary)
        record.content_digest = baseline
        save("copying")

        def started(pid):
            record.process_pid = pid
            save("copying")

        _run_process_group(
            [binary, "-a", "--checksum", "--", source_arg, str(payload)],
            stopped,
            started,
        )
        record.process_pid = None
        save("verifying")
        if _snapshot(payload, cancelled=stopped) != (baseline, size) or _snapshot(
            plan.source, cancelled=stopped
        ) != (baseline, size):
            raise ValueError("Independent content verification failed")
        record.verification_method = "content"
        save("verified")
        if stopped():
            raise DigestCancelled("Move cancelled before publication")
        if (
            plan.destination.parent.resolve() != plan.destination.parent
            or _identity(plan.destination.parent) != plan.parent_identity
        ):
            raise ValueError("Destination parent changed before publication")
        save("publishing")
        rename_noreplace(payload, plan.destination)
        record.destination_published = True
        sync_directory(plan.destination.parent)
        sync_directory(temporary)
        save("published")
        if stopped():
            raise DigestCancelled("Move cancelled before source retirement")
        if _identity(plan.source) != plan.source_identity:
            raise ValueError("Source identity changed before retirement")
        save("retiring")
        staged = stage_for_retention(
            plan.source,
            expected_digest=baseline,
            verification_target={
                "kind": "local",
                "path": str(plan.destination),
                "manifest_path": str(plan.manifest_path),
                "content_digest": baseline,
            },
            retention_days=7.0,
            registry_path=plan.manifest_path.parent.parent / "restorable-registry.json",
            cancelled=stopped,
        )
        record.staged_path = staged.staged_path
        record.finished_at = time.time()
        save("retired")
        return record
    except BaseException as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.finished_at = time.time()
        if isinstance(exc, _TerminationPending):
            record.finished_at = 0.0
            save("cancelling")
        else:
            record.process_pid = None
            save("cancelled" if isinstance(exc, DigestCancelled) else "failed")
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        error = MoveRecoveryRequired
        if isinstance(exc, DigestCancelled):
            error = MoveCancelled
        elif isinstance(exc, _TerminationPending):
            error = MoveInProgress
        raise error(
            "Move did not finish; inspect its retained transaction receipt", record
        ) from exc
    finally:
        if (
            temporary is not None
            and temporary.exists()
            and record.phase != "cancelling"
        ):
            shutil.rmtree(temporary)
