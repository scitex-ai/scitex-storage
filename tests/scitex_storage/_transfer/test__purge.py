"""Tests for the audited ``purge`` engine.

Holder and mount detection run against REAL processes and the real
``/proc/<pid>`` entries. The ``proc`` seam narrows the process table to the
processes a test owns, because a non-root runner cannot inspect other users'
processes -- and the engine rightly refuses when it cannot look.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scitex_storage._transfer import _purge
from scitex_storage._transfer._purge import (
    PurgeRefusedError,
    apply_purge,
    plan_purge,
    purge_log_dir,
)

REASON = "superseded by a newer root"


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    path.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = previous


@pytest.fixture
def proc(tmp_path):
    """A process table holding only this test process."""
    path = tmp_path / "proc"
    path.mkdir()
    (path / "self").symlink_to(f"/proc/{os.getpid()}")
    return path


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "work" / "stale-root"
    (root / "sub").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"a" * 8_192)
    (root / "sub" / "b.bin").write_bytes(b"b" * 4_096)
    return root


def _plan(tree, proc, *, reason=REASON, confirm=None):
    return plan_purge(
        tree,
        reason=reason,
        confirm=str(tree) if confirm is None else confirm,
        proc=proc,
    )


def _spawn(proc: Path, cwd: Path, code: str = "import time; time.sleep(30)"):
    child = subprocess.Popen([sys.executable, "-c", code], cwd=cwd)
    (proc / str(child.pid)).symlink_to(f"/proc/{child.pid}")
    time.sleep(0.3)
    return child


@pytest.fixture
def holder_in_tree(tree, proc):
    child = _spawn(proc, tree / "sub")
    yield child
    child.kill()
    child.wait()


def _apply_or_refusal(plan, proc):
    try:
        return apply_purge(plan, proc=proc)
    except PurgeRefusedError as exc:
        return exc


def test_plan_is_ready_for_an_unused_tree(tree, proc, home):
    # Arrange
    target = tree
    # Act
    plan = _plan(target, proc)
    # Assert
    assert plan.ready, plan.refusals


def test_plan_counts_every_entry(tree, proc, home):
    # Arrange
    target = tree
    # Act
    plan = _plan(target, proc)
    # Assert
    assert plan.entries == 4


def test_plan_deletes_nothing(tree, proc, home):
    # Arrange
    target = tree
    # Act
    _plan(target, proc)
    # Assert
    assert (target / "sub" / "b.bin").exists()


def test_plan_refuses_a_mismatched_confirm(tree, proc, home):
    # Arrange
    wrong = str(tree.parent)
    # Act
    plan = _plan(tree, proc, confirm=wrong)
    # Assert
    assert any("--confirm" in r for r in plan.refusals)


def test_plan_refuses_an_empty_reason(tree, proc, home):
    # Arrange
    blank = "  "
    # Act
    plan = _plan(tree, proc, reason=blank)
    # Assert
    assert any("--reason" in r for r in plan.refusals)


def test_plan_refuses_a_relative_path(proc, home):
    # Arrange
    relative = "stale-root"
    # Act
    plan = plan_purge(relative, reason=REASON, confirm=relative, proc=proc)
    # Assert
    assert any("absolute" in r for r in plan.refusals)


def test_plan_refuses_a_symlink(tree, proc, home):
    # Arrange
    link = tree.parent / "link"
    link.symlink_to(tree)
    # Act
    plan = _plan(link, proc)
    # Assert
    assert any("symlink" in r for r in plan.refusals)


def test_plan_refuses_a_top_level_path(proc, home):
    # Arrange
    top = "/tmp"
    # Act
    plan = plan_purge(top, reason=REASON, confirm=top, proc=proc)
    # Assert
    assert any("top-level" in r for r in plan.refusals)


def test_plan_refuses_an_ancestor_of_home(tmp_path, proc, home):
    # Arrange
    ancestor = tmp_path
    # Act
    plan = plan_purge(ancestor, reason=REASON, confirm=str(ancestor), proc=proc)
    # Assert
    assert any("home" in r for r in plan.refusals)


def test_plan_refuses_a_missing_path(tree, proc, home):
    # Arrange
    gone = tree.parent / "gone"
    # Act
    plan = _plan(gone, proc)
    # Assert
    assert any("nothing at" in r for r in plan.refusals)


def test_plan_refuses_a_process_whose_cwd_is_inside(tree, proc, home, holder_in_tree):
    # Arrange
    pid = holder_in_tree.pid
    # Act
    plan = _plan(tree, proc)
    # Assert
    assert any(f"pid {pid}" in r and "cwd=" in r for r in plan.refusals)


def test_plan_refuses_a_process_holding_an_open_file(tree, proc, home):
    # Arrange
    code = f"f = open({str(tree / 'a.bin')!r}); import time; time.sleep(30)"
    child = _spawn(proc, tree.parent, code)
    # Act
    try:
        plan = _plan(tree, proc)
    finally:
        child.kill()
        child.wait()
    # Assert
    assert any(f"pid {child.pid}" in r and "a.bin" in r for r in plan.refusals)


def test_plan_refuses_a_bind_mount_sourced_inside(tree, proc, home):
    # Arrange: another mount namespace bind-mounts tree/sub at /data.
    device = os.lstat(tree).st_dev
    inner = "/" + str(tree.relative_to(_purge._mount_point_of(tree)))
    other = proc / "4242"
    (other / "ns").mkdir(parents=True)
    (other / "ns" / "mnt").symlink_to("mnt:[4026599999]")
    (other / "mountinfo").write_text(
        f"900 1 {os.major(device)}:{os.minor(device)} {inner}/sub /data rw - ext4 /dev/x rw\n"
    )
    # Act
    plan = _plan(tree, proc)
    # Assert
    assert any("bind mount" in r and "/data" in r for r in plan.refusals)


def test_unescape_mount_decodes_octal_spaces():
    # Arrange
    escaped = r"/mnt/with\040space"
    # Act
    decoded = _purge._unescape_mount(escaped)
    # Assert
    assert decoded == "/mnt/with space"


def test_plan_counts_files_hard_linked_from_outside(tree, proc, home):
    # Arrange
    os.link(tree / "a.bin", tree.parent / "kept.bin")
    # Act
    plan = _plan(tree, proc)
    # Assert
    assert plan.hard_linked_outside == 1


def test_plan_counts_internal_hard_links_as_freed(tree, proc, home):
    # Arrange
    os.link(tree / "a.bin", tree / "sub" / "a-again.bin")
    # Act
    plan = _plan(tree, proc)
    # Assert
    assert plan.expected_bytes_freed == plan.allocated_bytes


def test_apply_deletes_the_tree(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    # Act
    apply_purge(plan, proc=proc)
    # Assert
    assert not tree.exists()


def test_apply_keeps_the_parent(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    # Act
    apply_purge(plan, proc=proc)
    # Assert
    assert tree.parent.is_dir()


def test_apply_writes_a_purged_audit_record(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    # Act
    audit = apply_purge(plan, proc=proc)
    # Assert
    record = json.loads(Path(audit["audit_log"]).read_text())
    assert (record["status"], record["path"], record["reason"]) == (
        "purged",
        str(tree),
        REASON,
    )


def test_apply_records_free_space_before_and_after(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    # Act
    audit = apply_purge(plan, proc=proc)
    # Assert
    assert (
        audit["bytes_freed"] == audit["free_bytes_after"] - audit["free_bytes_before"]
    )


def test_apply_audit_lives_under_purge_logs(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    # Act
    audit = apply_purge(plan, proc=proc)
    # Assert
    assert Path(audit["audit_log"]).parent == purge_log_dir()


def test_apply_refuses_a_holder_that_appeared_after_planning(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    child = _spawn(proc, tree)
    # Act
    try:
        outcome = _apply_or_refusal(plan, proc)
    finally:
        child.kill()
        child.wait()
    # Assert
    assert isinstance(outcome, PurgeRefusedError)


def test_apply_refusal_leaves_the_tree(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    child = _spawn(proc, tree)
    # Act
    try:
        _apply_or_refusal(plan, proc)
    finally:
        child.kill()
        child.wait()
    # Assert
    assert (tree / "a.bin").exists()


def test_apply_refusal_is_audited(tree, proc, home):
    # Arrange
    plan = _plan(tree, proc)
    child = _spawn(proc, tree)
    try:
        _apply_or_refusal(plan, proc)
    finally:
        child.kill()
        child.wait()
    # Act
    statuses = [
        json.loads(p.read_text())["status"] for p in purge_log_dir().glob("*.json")
    ]
    # Assert
    assert statuses == ["refused"]


def test_apply_without_an_audit_destination_deletes_nothing(tree, proc, home):
    # Arrange: the runtime directory is a file, so no audit can be written.
    runtime = home / ".scitex" / "scitex-storage"
    runtime.mkdir(parents=True)
    (runtime / "runtime").write_text("not a directory")
    # Act
    _apply_or_refusal(_plan(tree, proc), proc)
    # Assert
    assert (tree / "a.bin").exists()


# EOF
