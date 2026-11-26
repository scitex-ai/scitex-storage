"""Tests for scitex_storage._transfer._purge (audited regenerable deletion).

NO MOCKS: real temp trees, real deletes, a real sandboxed HOME for the audit
log. Holders are supplied as an explicit observed-path list where a test
needs one, which is the module's own seam for the pure comparison.
"""

import json
import os

import pytest

from scitex_storage._transfer._purge import (
    ELIGIBLE,
    REFUSED,
    apply_purge,
    list_runs,
    log_dir,
    plan_purge,
)


@pytest.fixture
def sandbox_home(tmp_path):
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


def _cache(root, name="uv-cache"):
    d = root / name
    d.mkdir()
    (d / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    (d / "blob").write_bytes(b"x" * 4096)
    return d


def _plain(root, name="job-temp"):
    d = root / name
    d.mkdir()
    (d / "out.txt").write_text("scratch")
    return d


def test_cachedir_tagged_tree_is_eligible(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    plan = plan_purge([d], run_id="r1", open_paths=[])
    # Assert
    assert plan.entries[0].decision == ELIGIBLE


def test_unclassified_tree_without_reason_is_refused(tmp_path, sandbox_home):
    # Arrange
    d = _plain(tmp_path)
    # Act
    plan = plan_purge([d], run_id="r1", open_paths=[])
    # Assert
    assert plan.entries[0].decision == REFUSED


def test_unclassified_tree_with_reason_is_eligible(tmp_path, sandbox_home):
    # Arrange
    d = _plain(tmp_path)
    # Act
    plan = plan_purge([d], run_id="r1", reason="CI job temp", open_paths=[])
    # Assert
    assert plan.entries[0].decision == ELIGIBLE


def test_tree_held_open_by_a_process_is_refused(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    holder = str((d / "blob").resolve())
    # Act
    plan = plan_purge([d], run_id="r1", open_paths=[holder])
    # Assert
    assert "live process" in plan.entries[0].refusal


def test_real_proc_scan_refuses_tree_that_is_this_process_cwd(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    previous = os.getcwd()
    os.chdir(d)
    # Act
    try:
        plan = plan_purge([d], run_id="r1")
    finally:
        os.chdir(previous)
    # Assert
    assert plan.entries[0].decision == REFUSED


def test_recent_tree_is_refused_by_min_age(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    plan = plan_purge([d], run_id="r1", min_age_days=3, open_paths=[])
    # Assert
    assert "younger than" in plan.entries[0].refusal


def test_old_tree_passes_min_age(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    old = 1_000_000_000
    for p in [d, *d.iterdir()]:
        os.utime(p, (old, old))
    # Act
    plan = plan_purge([d], run_id="r1", min_age_days=3, open_paths=[])
    # Assert
    assert plan.entries[0].decision == ELIGIBLE


def test_home_is_protected(tmp_path, sandbox_home):
    # Arrange
    _cache(sandbox_home, "x")
    # Act
    plan = plan_purge([sandbox_home], run_id="r1", reason="r", open_paths=[])
    # Assert
    assert "protected" in plan.entries[0].refusal


def test_plan_does_not_delete(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    plan_purge([d], run_id="r1", open_paths=[])
    # Assert
    assert d.exists()


def test_apply_deletes_eligible_tree(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    plan = plan_purge([d], run_id="r1", open_paths=[])
    # Act
    apply_purge(plan)
    # Assert
    assert not d.exists()


def test_apply_keeps_refused_tree(tmp_path, sandbox_home):
    # Arrange
    d = _plain(tmp_path)
    plan = plan_purge([d], run_id="r1", open_paths=[])
    # Act
    apply_purge(plan)
    # Assert
    assert d.exists()


def test_apply_writes_one_audit_record_per_path(tmp_path, sandbox_home):
    # Arrange
    a, b = _cache(tmp_path, "a"), _plain(tmp_path, "b")
    plan = plan_purge([a, b], run_id="r1", open_paths=[])
    # Act
    apply_purge(plan)
    # Assert
    assert len(list((log_dir() / "r1").glob("*.json"))) == 2


def test_audit_record_says_deleted_with_size(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    plan = plan_purge([d], run_id="r1", reason="uv cache", open_paths=[])
    # Act
    apply_purge(plan)
    record = json.loads((log_dir() / "r1" / "00000.json").read_text())
    # Assert
    assert (record["status"], record["reason"], record["size_bytes"] > 0) == (
        "deleted",
        "uv cache",
        True,
    )


def test_symlink_is_removed_without_touching_target(tmp_path, sandbox_home):
    # Arrange
    target = _cache(tmp_path, "real")
    link = tmp_path / "link"
    link.symlink_to(target)
    plan = plan_purge([link], run_id="r1", reason="stale link", open_paths=[])
    # Act
    apply_purge(plan)
    # Assert
    assert (not link.is_symlink(), (target / "blob").exists()) == (True, True)


def test_list_runs_reports_bytes_freed(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    apply_purge(plan_purge([d], run_id="r1", open_paths=[]))
    # Act
    runs = list_runs()
    # Assert
    assert runs[0]["bytes_freed"] > 0


def test_hardlinked_inode_is_counted_once(tmp_path, sandbox_home):
    # Arrange
    d = _plain(tmp_path, "site")
    (d / "big").write_bytes(b"y" * 65_536)
    os.link(d / "big", d / "big-link")
    single = plan_purge([d], run_id="r0", reason="r", open_paths=[]).entries[0]
    (d / "big-link").unlink()
    # Act
    without_link = plan_purge([d], run_id="r1", reason="r", open_paths=[]).entries[0]
    # Assert
    assert single.size_bytes == without_link.size_bytes
