"""Private, per-attempt audit records for SSH transfers."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from socket import gethostname
from uuid import uuid4

import scitex_logging as logging

from ._archive_transport import _manifest_dir
from ._durable import atomic_json

_log = logging.getLogger(__name__)
_current: ContextVar[tuple[Path, dict] | None] = ContextVar(
    "transfer_attempt", default=None
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_path() -> Path:
    return _manifest_dir().parent / "transfer-logs" / f"{uuid4().hex}.json"


def _write(path: Path, entry: dict) -> None:
    try:
        atomic_json(path, {**entry, "updated_at": _now()})
    except OSError as error:
        # A full disk must never hide the original transport failure.
        _log.warning("Could not write transfer audit %s: %s", path.name, error.strerror)


def _entry(operation: str, source, target: str, remote_path: str | None) -> dict:
    return {
        "schema": "scitex-storage.transfer-log/v1",
        "operation": operation,
        "local_host": gethostname(),
        "source": str(source),
        "destination": target,
        "remote_path": remote_path,
        "started_at": _now(),
    }


def refused_transfer(
    operation: str, source, target: str, remote_path: str | None, probe
) -> None:
    """Record an explicitly requested transfer refused before planning."""
    entry = _entry(operation, source, target, remote_path)
    details = probe.detail.lower()
    signatures = (
        ("permission denied", "ssh_authentication_failed"),
        ("host key verification failed", "ssh_host_key_rejected"),
        ("timed out", "ssh_timeout"),
        ("connection refused", "ssh_connection_refused"),
    )
    reason = next(
        (label for fragment, label in signatures if fragment in details),
        probe.verdict,
    )
    entry.update(
        status="refused", stage="ssh_preflight", reason=reason, finished_at=_now()
    )
    _write(_record_path(), entry)


def transfer_stage(stage: str, *, exit_code: int | None = None) -> None:
    attempt = _current.get()
    if attempt is None:
        return
    path, entry = attempt
    entry["stage"] = stage
    if exit_code is not None:
        entry["exit_code"] = exit_code
    _write(path, entry)


def logged_transfer(operation: str):
    """Keep a separate record for every retry, including API callers."""
    def decorate(function):
        @wraps(function)
        def run(plan, **kwargs):
            source = plan.manifest if operation == "restore" else plan
            verb = operation
            if operation == "archive" and kwargs.get("remove_source") is False:
                verb = "backup"
            path = _record_path()
            entry = _entry(verb, source.source, source.destination, source.remote_path)
            entry.update(
                status="running",
                stage="preflight",
                manifest_path=str(plan.manifest_path),
            )
            token = _current.set((path, entry))
            _write(path, entry)
            try:
                result = function(plan, **kwargs)
            except BaseException as error:
                status = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
                entry.update(status=status, error_type=type(error).__name__)
                raise
            else:
                entry.update(status="completed", stage="complete")
                if operation == "archive":
                    entry["verification_method"] = result.verification_method
                    entry["retained_source"] = result.retained_source
                return result
            finally:
                _current.reset(token)
                entry["finished_at"] = _now()
                _write(path, entry)
        return run
    return decorate
