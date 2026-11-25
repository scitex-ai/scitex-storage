"""Unit tests for scitex_storage._transfer._retire (verified source retirement).

Real temp trees and real hard links; the remote side goes through the same
fake-runner seam as the scrub tests, built from the source's own digests so a
"faithful" remote is byte-for-byte what was archived.
"""

import json
import os

import pytest

from scitex_storage._transfer._archive import ArchiveManifest
from scitex_storage._transfer._content_verify import digest_tree, tree_digest
from scitex_storage._transfer._retire import (
    RetireRefusedError,
    apply_retire,
    plan_retire,
)


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _tree(tmp_path):
    source = tmp_path / "data"
    (source / "sub").mkdir(parents=True)
    (source / "a.bin").write_bytes(b"alpha")
    (source / "sub" / "b.bin").write_bytes(b"bravo!")
    return source


def _mirror(source, sibling):
    for root, _dirs, names in os.walk(source):
        rel = os.path.relpath(root, source)
        os.makedirs(sibling / rel, exist_ok=True)
        for name in names:
            os.link(os.path.join(root, name), sibling / rel / name)


def _manifest(tmp_path, source, **overrides):
    digests = digest_tree(str(source))
    fields = dict(
        source=str(source),
        destination="scitex-nas-02",
        remote_path="/share/archive/data",
        size_bytes=11,
        file_count=2,
        checksummed=True,
        archived_at=0.0,
        verified="verified",
        verification_method="content",
        content_digest=tree_digest(digests),
        retained_source=True,
    )
    fields.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(ArchiveManifest(**fields).to_dict()))
    return path, digests


def _remote(digests, tally="2\n11\n"):
    listing = "".join(f"{h} ./{rel}\n" for rel, h in digests.digests.items())

    def run(cmd, **kwargs):
        joined = " ".join(str(p) for p in cmd)
        if "sha256sum" in joined:
            return _Done(0, listing)
        return _Done(0, tally)

    return run


@pytest.fixture(autouse=True)
def _home(tmp_path):
    """Real temp HOME so manifests and transfer logs never touch ~/.scitex."""
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path / "home")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev


def _attempt(plan, runner):
    """Apply, swallowing only a refusal so a test can inspect what remains."""
    try:
        apply_retire(plan, runner=runner)
    except RetireRefusedError:
        return


def _unreachable(cmd, **kwargs):
    return _Done(255, "", "ssh: connect refused")


def test_plan_proves_hard_linked_sibling(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    _mirror(source, tmp_path / "snap")
    path, _ = _manifest(tmp_path, source)
    # Act
    plan = plan_retire(path, (str(tmp_path / "snap"),))
    # Assert
    assert plan.ready


def test_plan_expects_all_bytes_freed_only_with_sibling(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    _mirror(source, tmp_path / "snap")
    path, _ = _manifest(tmp_path, source)
    # Act
    plan = plan_retire(path)
    # Assert
    assert plan.expected_bytes_freed == 0


def test_plan_refuses_sibling_with_extra_file(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    _mirror(source, tmp_path / "snap")
    (tmp_path / "snap" / "extra.txt").write_text("not archived")
    path, _ = _manifest(tmp_path, source)
    # Act
    plan = plan_retire(path, (str(tmp_path / "snap"),))
    # Assert
    assert plan.siblings[0].verdict == "mismatch"


def test_plan_refuses_sibling_with_copied_not_linked_file(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    (tmp_path / "copy").mkdir()
    (tmp_path / "copy" / "a.bin").write_bytes(b"alpha")
    path, _ = _manifest(tmp_path, source)
    # Act
    plan = plan_retire(path, (str(tmp_path / "copy"),))
    # Assert
    assert not plan.ready


def test_plan_refuses_tally_only_manifest(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, _ = _manifest(
        tmp_path, source, verification_method="tally", content_digest=""
    )
    # Act
    plan = plan_retire(path)
    # Assert
    assert not plan.ready


def test_plan_refuses_sibling_inside_source(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, _ = _manifest(tmp_path, source)
    # Act
    plan = plan_retire(path, (str(source / "sub"),))
    # Assert
    assert not plan.ready


def test_apply_deletes_source_and_sibling(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    _mirror(source, tmp_path / "snap")
    path, digests = _manifest(tmp_path, source)
    plan = plan_retire(path, (str(tmp_path / "snap"),))
    # Act
    apply_retire(plan, runner=_remote(digests))
    # Assert
    assert not source.exists() and not (tmp_path / "snap").exists()


def test_apply_records_retired_status_in_manifest(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, digests = _manifest(tmp_path, source)
    plan = plan_retire(path)
    # Act
    apply_retire(plan, runner=_remote(digests))
    # Assert
    assert json.loads(path.read_text())["retirement"]["status"] == "retired"


def test_apply_writes_completed_transfer_log(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, digests = _manifest(tmp_path, source)
    plan = plan_retire(path)
    # Act
    audit = apply_retire(plan, runner=_remote(digests))
    # Assert
    assert json.loads(open(audit["transfer_log"]).read())["status"] == "completed"


def test_apply_refuses_when_remote_content_drifted(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, digests = _manifest(tmp_path, source)
    digests.digests["a.bin"] = "0" * 64
    plan = plan_retire(path)
    # Act
    _attempt(plan, _remote(digests))
    # Assert
    assert source.exists()


def test_apply_refuses_when_remote_unreachable(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, _ = _manifest(tmp_path, source)
    plan = plan_retire(path)
    # Act
    _attempt(plan, _unreachable)
    # Assert
    assert source.exists()


def test_apply_refuses_when_source_changed_since_archive(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, digests = _manifest(tmp_path, source)
    (source / "a.bin").write_bytes(b"ALPHA")
    plan = plan_retire(path)
    # Act
    _attempt(plan, _remote(digests))
    # Assert
    assert source.exists()


def test_apply_records_refusal_in_manifest(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, _ = _manifest(tmp_path, source)
    plan = plan_retire(path)
    # Act
    _attempt(plan, _unreachable)
    # Assert
    assert json.loads(path.read_text())["retirement"]["status"] == "refused"


def test_plan_refuses_already_retired_manifest(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, digests = _manifest(tmp_path, source)
    apply_retire(plan_retire(path), runner=_remote(digests))
    source.mkdir()
    # Act
    plan = plan_retire(path)
    # Assert
    assert not plan.ready


def test_apply_raises_refusal_when_remote_unreachable(tmp_path):
    # Arrange
    source = _tree(tmp_path)
    path, _ = _manifest(tmp_path, source)
    plan = plan_retire(path)
    # Act / Assert
    with pytest.raises(RetireRefusedError):
        apply_retire(plan, runner=_unreachable)
