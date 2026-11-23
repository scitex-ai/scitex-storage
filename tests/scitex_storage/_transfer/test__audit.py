import json
from types import SimpleNamespace

import pytest

from scitex_storage._transfer import _audit


@pytest.fixture
def records(tmp_path, monkeypatch):
    manifest_dir = tmp_path / "archive-manifests"
    monkeypatch.setattr(_audit, "_manifest_dir", lambda: manifest_dir)
    return tmp_path / "transfer-logs"


def test_audit_records_failed_rsync_as_a_separate_retry(records, tmp_path):
    @_audit.logged_transfer("archive")
    def transfer(plan, *, remove_source=False):
        _audit.transfer_stage("rsync", exit_code=23)
        raise RuntimeError("Raw command details")

    plan = SimpleNamespace(
        source=tmp_path / "source",
        destination="scitex-nas-02",
        remote_path="/archive/source",
        manifest_path=tmp_path / "archive-manifests" / "source.json",
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            transfer(plan, remove_source=False)

    files = list(records.glob("*.json"))
    assert len(files) == 2
    for path in files:
        payload = json.loads(path.read_text())
        assert (
            payload["operation"], payload["status"], payload["stage"], payload["exit_code"],
            payload["error_type"], bool(payload["finished_at"]), path.stat().st_mode & 0o777,
        ) == ("backup", "failed", "rsync", 23, "RuntimeError", True, 0o600)
        assert "Raw command details" not in path.read_text()


def test_audit_records_ssh_refusal_without_remote_output(records):
    probe = SimpleNamespace(verdict="unreachable", detail="Permission denied (publickey)")
    _audit.refused_transfer("archive", "/data", "scitex-nas-02", None, probe)

    entry = json.loads(next(records.glob("*.json")).read_text())
    assert (entry["status"], entry["stage"], entry["reason"]) == (
        "refused", "ssh_preflight", "ssh_authentication_failed"
    )
