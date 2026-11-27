"""Tests for ``apply_restore(verify_content=True)`` and single-file restores.

The runner records argv and reports success without moving bytes, so the
local tree is whatever the test put there: that is exactly the case the
content check exists for (the pull "succeeded" and the data is not right).
"""

import hashlib
import subprocess
from pathlib import Path

from scitex_storage._transfer._archive import ArchiveManifest
from scitex_storage._transfer._restore import (
    RestoreNotVerifiedError,
    RestorePlan,
    apply_restore,
)


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")


def _manifest(source: Path, *, kind: str, digest: str) -> ArchiveManifest:
    return ArchiveManifest(
        source=str(source),
        destination="scitex-nas-02",
        remote_path="/remote/unit",
        size_bytes=4,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
        content_digest=digest,
        source_kind=kind,
    )


def _plan(manifest, tmp_path):
    return RestorePlan(manifest=manifest, manifest_path=tmp_path / "m.json")


def test_file_restore_pulls_the_single_entry(tmp_path):
    # Arrange
    source = tmp_path / "db.dump"
    runner = _Recorder()
    plan = _plan(_manifest(source, kind="file", digest="0" * 64), tmp_path)
    # Act
    apply_restore(plan, runner=runner)
    # Assert
    assert runner.calls[0][-2:] == ["scitex-nas-02:/remote/unit/db.dump", str(source)]


def test_verified_restore_accepts_matching_content(tmp_path):
    # Arrange
    source = tmp_path / "db.dump"
    source.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    plan = _plan(_manifest(source, kind="file", digest=digest), tmp_path)
    # Act
    restored = apply_restore(plan, verify_content=True, runner=_Recorder())
    # Assert
    assert restored == source


def test_verified_restore_refuses_different_content(tmp_path):
    # Arrange
    source = tmp_path / "db.dump"
    source.write_bytes(b"else")
    digest = hashlib.sha256(b"data").hexdigest()
    plan = _plan(_manifest(source, kind="file", digest=digest), tmp_path)
    # Act
    raised = False
    try:
        apply_restore(plan, verify_content=True, runner=_Recorder())
    except RestoreNotVerifiedError:
        raised = True
    # Assert
    assert raised


def test_refused_restore_never_deletes_the_remote_copy(tmp_path):
    # Arrange
    source = tmp_path / "db.dump"
    source.write_bytes(b"else")
    runner = _Recorder()
    plan = _plan(_manifest(source, kind="file", digest="0" * 64), tmp_path)
    # Act
    try:
        apply_restore(plan, verify_content=True, delete_remote=True, runner=runner)
    except RestoreNotVerifiedError:
        pass
    # Assert
    assert [c for c in runner.calls if c[0] == "ssh"] == []
