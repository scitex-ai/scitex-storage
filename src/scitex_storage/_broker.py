"""Typed, fail-closed Storage Broker planning contracts.

This module never executes filesystem or shell operations.  It validates
structured evidence and builds immutable plans for separately privileged
executors.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ResourceKind = Literal["home", "project", "dataset", "scratch"]
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _require_token(value: str, name: str) -> None:
    invalid = (
        not isinstance(value, str)
        or not _TOKEN.fullmatch(value)
        or value in {".", ".."}
    )
    if invalid:
        raise ValueError(f"{name} must be an opaque identifier, not a path or command")


class _ContractModel(BaseModel):
    """Strict immutable model used at every broker trust boundary."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        revalidate_instances="always",
    )


class StorageResource(_ContractModel):
    """Opaque resource identity accepted at the unprivileged broker boundary."""

    kind: ResourceKind
    resource_id: str

    @field_validator("resource_id")
    @classmethod
    def _validate_resource_id(cls, value: str) -> str:
        _require_token(value, "resource_id")
        return value


class StorageRoot(_ContractModel):
    """Trusted configuration that maps a resource kind to one canonical root."""

    kind: ResourceKind
    path: Path

    @field_validator("path")
    @classmethod
    def _validate_path(cls, path: Path) -> Path:
        if not path.is_absolute() or path.resolve(strict=False) != path:
            raise ValueError(
                "storage root must be an absolute canonical non-symlink path"
            )
        return path


class AuditContext(_ContractModel):
    """Required attribution carried unchanged into executor readback."""

    actor: str
    request_id: str
    reason: str

    @field_validator("actor", "request_id")
    @classmethod
    def _validate_token(cls, value: str, info) -> str:
        _require_token(value, info.field_name)
        return value

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, reason: str) -> str:
        if not reason.strip() or any(ord(char) < 32 for char in reason):
            raise ValueError("reason must be non-empty printable text")
        return reason


class BrokerPolicy(_ContractModel):
    """Allowlisted roots and managed POSIX identity range."""

    roots: tuple[StorageRoot, ...]
    uid_min: int
    uid_max: int
    allowed_modes: tuple[int, ...] = (0o700, 0o750, 0o770, 0o2770)

    @model_validator(mode="after")
    def _validate_policy(self):
        kinds = [root.kind for root in self.roots]
        if not kinds or len(kinds) != len(set(kinds)):
            raise ValueError("roots must contain one unique allowlisted root per kind")
        if self.uid_min < 1 or self.uid_max < self.uid_min:
            raise ValueError("managed UID/GID range is invalid")
        if not self.allowed_modes or len(self.allowed_modes) != len(
            set(self.allowed_modes)
        ):
            raise ValueError("allowed_modes must be non-empty and unique")
        return self

    def root_for(self, kind: ResourceKind) -> Path:
        for root in self.roots:
            if root.kind == kind:
                return root.path
        raise ValueError(f"resource kind {kind!r} has no allowlisted root")


class StorageRequest(_ContractModel):
    """Desired state. Deliberately has no caller-supplied path or command."""

    resource: StorageResource
    owner_uid: int = Field(gt=0)
    owner_gid: int = Field(gt=0)
    mode: int
    quota_bytes: int = Field(gt=0)
    quota_inodes: int = Field(gt=0)
    audit: AuditContext


class StoragePlan(_ContractModel):
    """Validated preview; privileged executors accept requests, never plans."""

    resource: StorageResource
    canonical_root: Path
    target: Path
    policy_uid_min: int = Field(gt=0)
    policy_uid_max: int = Field(gt=0)
    allowed_modes: tuple[int, ...]
    owner_uid: int = Field(gt=0)
    owner_gid: int = Field(gt=0)
    mode: int
    quota_bytes: int = Field(gt=0)
    quota_inodes: int = Field(gt=0)
    audit: AuditContext
    idempotency_key: str
    nofollow_required: bool = True

    @field_validator("idempotency_key")
    @classmethod
    def _validate_idempotency_key(cls, value: str) -> str:
        if not _HEX64.fullmatch(value):
            raise ValueError("idempotency_key must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_plan(self):
        root_ok = (
            self.canonical_root.is_absolute()
            and self.canonical_root.resolve(strict=False) == self.canonical_root
        )
        target_ok = (
            self.target.is_absolute()
            and self.target.parent == self.canonical_root
            and self.target.resolve(strict=False) == self.target
            and not self.target.is_symlink()
        )
        policy_ok = (
            self.policy_uid_min <= self.policy_uid_max
            and self.policy_uid_min <= self.owner_uid <= self.policy_uid_max
            and self.policy_uid_min <= self.owner_gid <= self.policy_uid_max
            and self.mode in self.allowed_modes
            and bool(self.allowed_modes)
            and self.nofollow_required is True
        )
        if not root_ok or not target_ok:
            raise ValueError("plan target is outside its canonical non-symlink root")
        if not policy_ok:
            raise ValueError("plan weakens managed identity, mode, or no-follow policy")
        return self


class StorageReadback(_ContractModel):
    """Structured post-operation state returned by a privileged executor."""

    resource: StorageResource
    canonical_root: Path
    target: Path
    owner_uid: int = Field(gt=0)
    owner_gid: int = Field(gt=0)
    mode: int
    quota_bytes: int = Field(gt=0)
    quota_inodes: int = Field(gt=0)
    audit_request_id: str
    idempotency_key: str
    nofollow_verified: bool
    resolution_method: Literal["openat2-beneath-no-symlinks"]
    device_id: int = Field(ge=0)
    inode: int = Field(gt=0)

    @field_validator("audit_request_id")
    @classmethod
    def _validate_audit_request_id(cls, value: str) -> str:
        _require_token(value, "audit_request_id")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _validate_readback_key(cls, value: str) -> str:
        if not _HEX64.fullmatch(value):
            raise ValueError("idempotency_key must be a SHA-256 hex digest")
        return value


@runtime_checkable
class NasExecutor(Protocol):
    """Capability boundary for NAS-side provision/chown/quota operations."""

    def apply(self, request: StorageRequest) -> StorageReadback: ...

    def readback(self, resource: StorageResource) -> StorageReadback: ...


@runtime_checkable
class NodeProjector(Protocol):
    """Capability boundary for node-side mount and projection operations."""

    def project(self, request: StorageRequest) -> StorageReadback: ...

    def readback(self, resource: StorageResource) -> StorageReadback: ...


class StorageContractEvidence(_ContractModel):
    """Read-only runtime evidence required before reporting broker readiness."""

    nas_authority: str
    projector_authority: str
    root_kinds: tuple[ResourceKind, ...]
    authoritative_identity: bool
    root_squash: bool
    quota_readback: bool
    projection_readback: bool
    rollback_path: bool

    @field_validator("nas_authority", "projector_authority")
    @classmethod
    def _validate_authority(cls, value: str, info) -> str:
        _require_token(value, info.field_name)
        return value

    @model_validator(mode="after")
    def _validate_evidence(self):
        if self.nas_authority == self.projector_authority:
            raise ValueError("NAS and projector authorities must be distinct")
        if len(self.root_kinds) != len(set(self.root_kinds)):
            raise ValueError("root_kinds must be unique")
        return self


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
        "schema_version": 1,
        "kind": request.resource.kind,
        "resource_id": request.resource.resource_id,
        "canonical_root": str(root),
        "target": str(target),
        "policy_uid_min": policy.uid_min,
        "policy_uid_max": policy.uid_max,
        "allowed_modes": list(policy.allowed_modes),
        "owner_uid": request.owner_uid,
        "owner_gid": request.owner_gid,
        "mode": request.mode,
        "quota_bytes": request.quota_bytes,
        "quota_inodes": request.quota_inodes,
        "audit": request.audit.model_dump(mode="json"),
        "nofollow_required": True,
    }
    idempotency_key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return StoragePlan(
        resource=request.resource,
        canonical_root=root,
        target=target,
        policy_uid_min=policy.uid_min,
        policy_uid_max=policy.uid_max,
        allowed_modes=policy.allowed_modes,
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
    try:
        plan = StoragePlan.model_validate(plan.model_dump())
    except (ValidationError, ValueError):
        return {
            "schema_version": 1,
            "operation": "storage.readback.validate",
            "mutating": False,
            "ready": False,
            "checks": [],
            "blockers": ["plan_validation"],
        }
    try:
        readback = StorageReadback.model_validate(readback.model_dump())
    except (ValidationError, ValueError):
        return {
            "schema_version": 1,
            "operation": "storage.readback.validate",
            "mutating": False,
            "ready": False,
            "checks": [],
            "blockers": ["readback_validation"],
        }
    observed = (
        ("resource", readback.resource == plan.resource),
        ("canonical_root", readback.canonical_root == plan.canonical_root),
        ("target", readback.target == plan.target),
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
        (
            "resolution_method",
            readback.resolution_method == "openat2-beneath-no-symlinks",
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
    try:
        evidence = StorageContractEvidence.model_validate(evidence.model_dump())
    except (ValidationError, ValueError):
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
        (
            "distinct_authorities",
            evidence.nas_authority != evidence.projector_authority,
        ),
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
    blockers.append("trusted_runtime_collector")
    return {
        "schema_version": 1,
        "operation": "storage.contract.validate",
        "mutating": False,
        "ready": False,
        "checks": checks,
        "blockers": blockers,
    }
