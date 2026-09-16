"""Typed, fail-closed Storage Broker planning contracts.

This module never executes filesystem or shell operations.  It validates
structured evidence and builds immutable plans for separately privileged
executors.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

ResourceKind = Literal["home", "project", "dataset", "scratch"]
_KINDS = frozenset({"home", "project", "dataset", "scratch"})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _require_token(value: str, name: str) -> None:
    invalid = (
        not isinstance(value, str)
        or not _TOKEN.fullmatch(value)
        or value in {".", ".."}
    )
    if invalid:
        raise ValueError(f"{name} must be an opaque identifier, not a path or command")


@dataclass(frozen=True)
class StorageResource:
    """Opaque resource identity accepted at the unprivileged broker boundary."""

    kind: ResourceKind
    resource_id: str

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown resource kind: {self.kind!r}")
        _require_token(self.resource_id, "resource_id")


@dataclass(frozen=True)
class StorageRoot:
    """Trusted configuration that maps a resource kind to one canonical root."""

    kind: ResourceKind
    path: Path

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown storage root kind: {self.kind!r}")
        path = Path(self.path)
        if not path.is_absolute() or path.resolve(strict=False) != path:
            raise ValueError(
                "storage root must be an absolute canonical non-symlink path"
            )
        object.__setattr__(self, "path", path)


@dataclass(frozen=True)
class AuditContext:
    """Required attribution carried unchanged into executor readback."""

    actor: str
    request_id: str
    reason: str

    def __post_init__(self) -> None:
        _require_token(self.actor, "actor")
        _require_token(self.request_id, "request_id")
        if not self.reason.strip() or any(ord(char) < 32 for char in self.reason):
            raise ValueError("reason must be non-empty printable text")


@dataclass(frozen=True)
class BrokerPolicy:
    """Allowlisted roots and managed POSIX identity range."""

    roots: tuple[StorageRoot, ...]
    uid_min: int
    uid_max: int
    allowed_modes: tuple[int, ...] = (0o700, 0o750, 0o770, 0o2770)

    def __post_init__(self) -> None:
        kinds = [root.kind for root in self.roots]
        if not kinds or len(kinds) != len(set(kinds)):
            raise ValueError("roots must contain one unique allowlisted root per kind")
        if self.uid_min < 1 or self.uid_max < self.uid_min:
            raise ValueError("managed UID/GID range is invalid")

    def root_for(self, kind: ResourceKind) -> Path:
        for root in self.roots:
            if root.kind == kind:
                return root.path
        raise ValueError(f"resource kind {kind!r} has no allowlisted root")


@dataclass(frozen=True)
class StorageRequest:
    """Desired state. Deliberately has no caller-supplied path or command."""

    resource: StorageResource
    owner_uid: int
    owner_gid: int
    mode: int
    quota_bytes: int
    quota_inodes: int
    audit: AuditContext

    def __post_init__(self) -> None:
        if self.quota_bytes <= 0 or self.quota_inodes <= 0:
            raise ValueError("quota values must be positive")


@dataclass(frozen=True)
class StoragePlan:
    """Immutable desired state for a narrow privileged executor."""

    resource: StorageResource
    target: Path
    owner_uid: int
    owner_gid: int
    mode: int
    quota_bytes: int
    quota_inodes: int
    audit: AuditContext
    idempotency_key: str
    nofollow_required: bool = True


@dataclass(frozen=True)
class StorageReadback:
    """Structured post-operation state returned by a privileged executor."""

    resource: StorageResource
    owner_uid: int
    owner_gid: int
    mode: int
    quota_bytes: int
    quota_inodes: int
    audit_request_id: str
    idempotency_key: str
    nofollow_verified: bool


@runtime_checkable
class NasExecutor(Protocol):
    """Capability boundary for NAS-side provision/chown/quota operations."""

    def apply(self, plan: StoragePlan) -> StorageReadback: ...

    def readback(self, resource: StorageResource) -> StorageReadback: ...


@runtime_checkable
class NodeProjector(Protocol):
    """Capability boundary for node-side mount and projection operations."""

    def project(self, plan: StoragePlan) -> StorageReadback: ...

    def readback(self, resource: StorageResource) -> StorageReadback: ...


@dataclass(frozen=True)
class StorageContractEvidence:
    """Read-only runtime evidence required before reporting broker readiness."""

    nas_executor: object
    node_projector: object
    root_kinds: tuple[ResourceKind, ...]
    authoritative_identity: bool
    root_squash: bool
    quota_readback: bool
    projection_readback: bool
    rollback_path: bool


def plan_storage(request: StorageRequest, policy: BrokerPolicy) -> StoragePlan:
    """Resolve one opaque request beneath its configured canonical root."""
    identities = (("owner_uid", request.owner_uid), ("owner_gid", request.owner_gid))
    for name, value in identities:
        if not policy.uid_min <= value <= policy.uid_max:
            raise ValueError(f"{name} is outside the managed range")
    if request.mode not in policy.allowed_modes:
        raise ValueError("mode is not allowlisted")

    root = policy.root_for(request.resource.kind)
    target = root / request.resource.resource_id
    target.relative_to(root)
    if target.is_symlink():
        raise ValueError("resource target must not be a symlink")
    payload = {
        "kind": request.resource.kind,
        "resource_id": request.resource.resource_id,
        "owner_uid": request.owner_uid,
        "owner_gid": request.owner_gid,
        "mode": request.mode,
        "quota_bytes": request.quota_bytes,
        "quota_inodes": request.quota_inodes,
        "request_id": request.audit.request_id,
    }
    idempotency_key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return StoragePlan(
        resource=request.resource,
        target=target,
        owner_uid=request.owner_uid,
        owner_gid=request.owner_gid,
        mode=request.mode,
        quota_bytes=request.quota_bytes,
        quota_inodes=request.quota_inodes,
        audit=request.audit,
        idempotency_key=idempotency_key,
    )


def validate_readback(
    plan: StoragePlan, readback: StorageReadback
) -> dict[str, Any]:
    """Compare executor readback with every security-relevant desired field."""
    observed = (
        ("resource", readback.resource == plan.resource),
        ("owner_uid", readback.owner_uid == plan.owner_uid),
        ("owner_gid", readback.owner_gid == plan.owner_gid),
        ("mode", readback.mode == plan.mode),
        ("quota_bytes", readback.quota_bytes == plan.quota_bytes),
        ("quota_inodes", readback.quota_inodes == plan.quota_inodes),
        ("audit_request_id", readback.audit_request_id == plan.audit.request_id),
        ("idempotency_key", readback.idempotency_key == plan.idempotency_key),
        (
            "nofollow",
            plan.nofollow_required is True and readback.nofollow_verified is True,
        ),
    )
    checks = [
        {"name": name, "ok": ok, "observed": ok, "expected": True}
        for name, ok in observed
    ]
    blockers = [name for name, ok in observed if not ok]
    return {
        "schema_version": 1,
        "operation": "storage.readback.validate",
        "mutating": False,
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
    }


def validate_storage_contract(
    evidence: StorageContractEvidence | object | None = None,
) -> dict[str, Any]:
    """Return a structured, fail-closed runtime-capability verdict."""
    if evidence is None:
        return {
            "schema_version": 1,
            "operation": "storage.contract.validate",
            "mutating": False,
            "ready": False,
            "checks": [],
            "blockers": ["runtime_evidence"],
        }
    if not isinstance(evidence, StorageContractEvidence):
        return {
            "schema_version": 1,
            "operation": "storage.contract.validate",
            "mutating": False,
            "ready": False,
            "checks": [],
            "blockers": ["typed_runtime_evidence"],
        }

    expected_roots = {"home", "project", "dataset"}
    roots_ok = (
        len(evidence.root_kinds) == len(expected_roots)
        and set(evidence.root_kinds) == expected_roots
    )
    observed = (
        ("nas_executor", isinstance(evidence.nas_executor, NasExecutor)),
        ("node_projector", isinstance(evidence.node_projector, NodeProjector)),
        ("canonical_roots", roots_ok),
        ("authoritative_identity", evidence.authoritative_identity is True),
        ("root_squash", evidence.root_squash is True),
        ("quota_readback", evidence.quota_readback is True),
        ("projection_readback", evidence.projection_readback is True),
        ("rollback_path", evidence.rollback_path is True),
    )
    checks = [
        {"name": name, "ok": ok, "observed": ok, "expected": True}
        for name, ok in observed
    ]
    blockers = [name for name, ok in observed if not ok]
    return {
        "schema_version": 1,
        "operation": "storage.contract.validate",
        "mutating": False,
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
    }
