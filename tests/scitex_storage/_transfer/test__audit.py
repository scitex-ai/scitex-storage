import json
import os
from types import SimpleNamespace

import pytest

from scitex_storage._transfer import _audit


@pytest.fixture
def sandboxed_home(tmp_path):
    """A real, private HOME for the audit log directory.

    The records used to land in the runner's REAL ~/.scitex transfer-logs,
    shared by every xdist worker. One worker's reader globbed a record that
    another worker then unlinked, and the read raised FileNotFoundError (the
    v0.6.19 release gate, py3.11). A per-test HOME removes the shared state
    instead of tolerating the race, and keeps tests out of the operator's home.
    """
    home = tmp_path / "home"
    home.mkdir()
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev


@pytest.fixture
def read_records(tmp_path, sandboxed_home):
    def read():
        records = []
        for path in sorted(_audit._record_path().parent.glob("*.json")):
            content = path.read_text()
            payload = json.loads(content)
            if payload["source"] == str(tmp_path):
                records.append((payload, path.stat().st_mode & 0o777, content))
                path.unlink()
        return records

    return read


def test_audit_records_failed_rsync_as_a_separate_retry(read_records, tmp_path):
    # Arrange
    @_audit.logged_transfer("archive")
    def transfer(plan, *, remove_source=False):
        _audit.transfer_stage("rsync", exit_code=23)
        raise RuntimeError("Raw command details")

    plan = SimpleNamespace(
        source=tmp_path,
        destination="scitex-nas-02",
        remote_path="/archive/source",
        manifest_path=tmp_path / "source.json",
    )

    # Act
    for _ in range(2):
        try:
            transfer(plan, remove_source=False)
        except RuntimeError:
            pass

    # Assert
    assert [
        (
            payload["operation"],
            payload["status"],
            payload["stage"],
            payload["exit_code"],
            payload["error_type"],
            bool(payload["finished_at"]),
            mode,
            "Raw command details" in content,
        )
        for payload, mode, content in read_records()
    ] == [("backup", "failed", "rsync", 23, "RuntimeError", True, 0o600, False)] * 2


def test_audit_records_ssh_refusal_without_remote_output(read_records, tmp_path):
    # Arrange
    probe = SimpleNamespace(
        verdict="unreachable", detail="Permission denied (publickey)"
    )

    # Act
    _audit.refused_transfer("archive", tmp_path, "scitex-nas-02", None, probe)

    # Assert
    assert [
        (entry["status"], entry["stage"], entry["reason"], probe.detail in content)
        for entry, _, content in read_records()
    ] == [("refused", "ssh_preflight", "ssh_authentication_failed", False)]
