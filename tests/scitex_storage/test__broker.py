"""Fail-closed contract tests for the Hub Storage Broker capability."""

import os

import pytest
from pydantic import ValidationError

from scitex_storage import (
    AuditContext,
    BrokerPolicy,
    NasExecutor,
    NodeProjector,
    StorageContractEvidence,
    StoragePlan,
    StorageReadback,
    StorageRequest,
    StorageResource,
    StorageRoot,
    plan_storage,
    validate_readback,
    validate_storage_contract,
)


def test_contract_is_not_ready_without_runtime_evidence():
    # Arrange
    expected = {
        "schema_version": 1,
        "operation": "storage.contract.validate",
        "mutating": False,
        "ready": False,
        "checks": [],
        "blockers": ["runtime_evidence"],
    }
    # Act
    report = validate_storage_contract()
    # Assert
    assert report == expected


@pytest.mark.parametrize(
    "resource_id",
    ["../alice", "alice/home", r"alice\home", ".", "..", "$(id)", "alice;id"],
)
def test_resource_id_rejects_paths_and_shell_fragments(resource_id):
    # Arrange
    kind = "home"
    # Act
    # Assert
    with pytest.raises(ValueError, match="resource_id"):
        StorageResource(kind=kind, resource_id=resource_id)


def test_storage_root_rejects_symlink(tmp_path):
    # Arrange
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    # Act
    # Assert
    with pytest.raises(ValueError, match="canonical"):
        StorageRoot(kind="home", path=link)


def test_plan_is_confined_and_idempotent(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    policy = BrokerPolicy(
        roots=(StorageRoot(kind="home", path=root),),
        uid_min=20_000,
        uid_max=59_999,
    )
    request = StorageRequest(
        resource=StorageResource(kind="home", resource_id="alice-01"),
        owner_uid=20_001,
        owner_gid=20_001,
        mode=0o700,
        quota_bytes=32 * 1024**3,
        quota_inodes=1_000_000,
        audit=AuditContext(
            actor="hub-service",
            request_id="req-01",
            reason="provision beta home",
        ),
    )
    # Act
    first = plan_storage(request, policy)
    second = plan_storage(request, policy)
    # Assert
    assert (first.target, first.idempotency_key, first.audit) == (
        root / "alice-01",
        second.idempotency_key,
        request.audit,
    )


def _request(resource_id="alice-01", *, uid=20_001, mode=0o700):
    return StorageRequest(
        resource=StorageResource(kind="home", resource_id=resource_id),
        owner_uid=uid,
        owner_gid=uid,
        mode=mode,
        quota_bytes=1024,
        quota_inodes=10,
        audit=AuditContext(actor="hub-service", request_id="req-02", reason="test"),
    )


def _policy(root):
    return BrokerPolicy(
        roots=(StorageRoot(kind="home", path=root),),
        uid_min=20_000,
        uid_max=59_999,
    )


def _live_request():
    return StorageRequest(
        resource=StorageResource(kind="home", resource_id="live-user"),
        owner_uid=os.getuid(),
        owner_gid=os.getgid(),
        mode=0o700,
        quota_bytes=1024,
        quota_inodes=10,
        audit=AuditContext(actor="hub-service", request_id="req-live", reason="test"),
    )


def _live_policy(root):
    return BrokerPolicy(
        roots=(StorageRoot(kind="home", path=root),),
        uid_min=min(os.getuid(), os.getgid()),
        uid_max=max(os.getuid(), os.getgid()),
    )


def test_plan_rejects_existing_symlink_target(tmp_path):
    # Arrange
    root = tmp_path / "home"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "alice-01").symlink_to(outside, target_is_directory=True)
    # Act
    # Assert
    with pytest.raises(ValueError, match="symlink"):
        plan_storage(_request(), _policy(root))


@pytest.mark.parametrize("uid", [19_999, 60_000])
def test_plan_rejects_identity_outside_managed_range(tmp_path, uid):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    # Act
    # Assert
    with pytest.raises(ValueError, match="managed range"):
        plan_storage(_request(uid=uid), _policy(root))


def test_plan_rejects_unallowlisted_mode(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    # Act
    # Assert
    with pytest.raises(ValueError, match="mode"):
        plan_storage(_request(mode=0o777), _policy(root))


class _Nas:
    def apply(self, request):
        return None

    def readback(self, resource):
        return None


class _Projector:
    def project(self, request):
        return None

    def readback(self, resource):
        return None


def test_contract_remains_blocked_without_trusted_runtime_collector():
    # Arrange
    evidence = StorageContractEvidence(
        nas_authority="nas-provisioner-v1",
        projector_authority="node-projector-v1",
        root_kinds=("home", "project", "dataset"),
        authoritative_identity=True,
        root_squash=True,
        quota_readback=True,
        projection_readback=True,
        rollback_path=True,
    )
    # Act
    report = validate_storage_contract(evidence)
    # Assert
    assert (report["ready"], report["blockers"]) == (
        False,
        ["trusted_runtime_collector"],
    )


def test_mapping_cannot_self_attest_runtime_readiness():
    # Arrange
    evidence = {"ready": True, "shell": "chown -R 20001:20001 /"}
    # Act
    report = validate_storage_contract(evidence)
    # Assert
    assert (report["ready"], report["blockers"]) == (
        False,
        ["typed_runtime_evidence"],
    )


def test_executor_capabilities_are_separate():
    # Arrange
    nas = _Nas()
    projector = _Projector()
    # Act
    observed = (
        isinstance(nas, NasExecutor),
        isinstance(nas, NodeProjector),
        isinstance(projector, NodeProjector),
        isinstance(projector, NasExecutor),
    )
    # Assert
    assert observed == (True, False, True, False)


def test_readback_has_no_raw_command_surface():
    # Arrange
    dangerous = {"argv", "command", "shell", "path"}
    # Act
    fields = set(StorageReadback.model_fields)
    # Assert
    assert fields.isdisjoint(dangerous)


def test_plan_requires_nofollow(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    # Act
    plan = plan_storage(_request(), _policy(root))
    # Assert
    assert plan.nofollow_required is True


def _readback(plan, *, owner_uid=None):
    stat = plan.target.lstat()
    return StorageReadback(
        resource=plan.resource,
        canonical_root=plan.canonical_root,
        target=plan.target,
        owner_uid=plan.owner_uid if owner_uid is None else owner_uid,
        owner_gid=plan.owner_gid,
        mode=plan.mode,
        quota_bytes=plan.quota_bytes,
        quota_inodes=plan.quota_inodes,
        audit_request_id=plan.audit.request_id,
        idempotency_key=plan.idempotency_key,
        nofollow_verified=True,
        resolution_method="openat2-beneath-no-symlinks",
        object_type="directory",
        device_id=stat.st_dev,
        inode=stat.st_ino,
    )


def test_readback_verification_accepts_exact_state(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    request = _live_request()
    policy = _live_policy(root)
    plan = plan_storage(request, policy)
    plan.target.mkdir(mode=plan.mode)
    # Act
    report = validate_readback(request, policy, _readback(plan))
    # Assert
    assert (report["ready"], report["blockers"]) == (True, [])


def test_readback_verification_rejects_owner_mismatch(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    request = _live_request()
    policy = _live_policy(root)
    plan = plan_storage(request, policy)
    plan.target.mkdir(mode=plan.mode)
    # Act
    report = validate_readback(
        request, policy, _readback(plan, owner_uid=plan.owner_uid + 1)
    )
    # Assert
    assert (report["ready"], report["blockers"]) == (False, ["owner_uid"])


def test_plan_model_copy_cannot_keep_stale_digest(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    plan = plan_storage(_request(), _policy(root)).model_copy(
        update={
            "audit": AuditContext(
                actor="attacker",
                request_id="req-02",
                reason="changed after digest",
            )
        }
    )
    # Act
    # Assert
    with pytest.raises(ValidationError, match="idempotency"):
        StoragePlan.model_validate(plan.model_dump())


def test_request_forbids_undeclared_command_fields():
    # Arrange
    payload = {
        "resource": {"kind": "home", "resource_id": "alice-01"},
        "owner_uid": 20_001,
        "owner_gid": 20_001,
        "mode": 0o700,
        "quota_bytes": 1024,
        "quota_inodes": 10,
        "audit": {"actor": "hub-service", "request_id": "req-03", "reason": "test"},
        "shell": "chown -R 20001:20001 /",
    }
    # Act
    # Assert
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StorageRequest.model_validate(payload)


def test_request_uses_strict_identity_types():
    # Arrange
    payload = {
        "resource": {"kind": "home", "resource_id": "alice-01"},
        "owner_uid": "20001",
        "owner_gid": 20_001,
        "mode": 0o700,
        "quota_bytes": 1024,
        "quota_inodes": 10,
        "audit": {"actor": "hub-service", "request_id": "req-04", "reason": "test"},
    }
    # Act
    # Assert
    with pytest.raises(ValidationError, match="valid integer"):
        StorageRequest.model_validate(payload)


def test_request_json_schema_forbids_extra_properties():
    # Arrange
    expected = False
    # Act
    additional = StorageRequest.model_json_schema()["additionalProperties"]
    # Assert
    assert additional is expected


def test_direct_plan_construction_rejects_privileged_escape(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    payload = plan_storage(_request(), _policy(root)).model_dump()
    payload.update(
        {
            "target": tmp_path / "outside",
            "owner_uid": 0,
            "owner_gid": 0,
            "mode": 0o777,
            "nofollow_required": False,
        }
    )
    # Act
    # Assert
    with pytest.raises(ValidationError):
        StoragePlan.model_validate(payload)


def test_readback_revalidates_root_after_symlink_replacement(tmp_path):
    # Arrange
    root = tmp_path / "home"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    request = _live_request()
    policy = _live_policy(root)
    plan = plan_storage(request, policy)
    plan.target.mkdir(mode=plan.mode)
    readback = _readback(plan)
    plan.target.rmdir()
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)
    # Act
    report = validate_readback(request, policy, readback)
    # Assert
    assert report["blockers"] == ["plan_validation"]


def test_readback_binds_live_filesystem_identity(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    request = _live_request()
    policy = _live_policy(root)
    plan = plan_storage(request, policy)
    plan.target.mkdir(mode=plan.mode)
    readback = _readback(plan).model_copy(
        update={"inode": plan.target.lstat().st_ino + 1}
    )
    # Act
    report = validate_readback(request, policy, readback)
    # Assert
    assert report["blockers"] == ["filesystem_identity"]


def test_readback_detects_live_permission_drift(tmp_path):
    # Arrange
    root = tmp_path / "home"
    root.mkdir()
    request = _live_request()
    policy = _live_policy(root)
    plan = plan_storage(request, policy)
    plan.target.mkdir(mode=plan.mode)
    readback = _readback(plan)
    plan.target.chmod(0o777)
    # Act
    report = validate_readback(request, policy, readback)
    # Assert
    assert report["blockers"] == ["live_metadata"]


def test_idempotency_key_binds_canonical_root(tmp_path):
    # Arrange
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    # Act
    first = plan_storage(_request(), _policy(first_root))
    second = plan_storage(_request(), _policy(second_root))
    # Assert
    assert first.idempotency_key != second.idempotency_key


def test_runtime_evidence_is_json_serializable():
    # Arrange
    evidence = StorageContractEvidence(
        nas_authority="nas-provisioner-v1",
        projector_authority="node-projector-v1",
        root_kinds=("home", "project", "dataset"),
        authoritative_identity=True,
        root_squash=True,
        quota_readback=True,
        projection_readback=True,
        rollback_path=True,
    )
    # Act
    payload = evidence.model_dump_json()
    # Assert
    assert '"nas_authority":"nas-provisioner-v1"' in payload
