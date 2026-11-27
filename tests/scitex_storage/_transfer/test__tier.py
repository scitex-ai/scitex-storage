"""Tests for ``apply_tier`` / ``restore_tiered`` paths that need no transport.

The happy path (real rsync, real read-back, pointer, restore, sweep) lives in
tests/integration/test_tier_roundtrip.py. Here the runner is a transport that
fails every command, which is the honest double for "the NAS is down": the
unit must fail, keep its source, and get no pointer.
"""

import json
import os
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import pytest

from scitex_storage._transfer._tier import (
    POINTER_SCHEMA,
    apply_tier,
    plan_tier,
    pointer_path_for,
    read_pointer,
    restore_tiered,
)

DAY = 86_400
Usage = namedtuple("Usage", "total used free")


def _down(command, **kwargs):
    """A transport whose every command fails, as an unreachable host does."""
    return subprocess.CompletedProcess(command, 255, "", "ssh: connect: refused")


@pytest.fixture
def world(tmp_path):
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path / "home")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "self").symlink_to(f"/proc/{os.getpid()}")
    volume = tmp_path / "volume"
    volume.mkdir()
    path = volume / "old.dump"
    path.write_bytes(b"x" * 500)
    stamp = time.time() - 400 * DAY
    os.utime(path, (stamp, stamp))
    plan = plan_tier(
        volume,
        destination="scitex-nas-02",
        remote_root=str(tmp_path / "remote"),
        cold_after_days=30,
        min_size_bytes=100,
        disk_usage=lambda _p: Usage(10_000, 9_000, 1_000),
        proc=proc,
    )
    try:
        yield plan, path, proc
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def test_unreachable_host_fails_the_unit(world):
    # Arrange
    plan, _path, proc = world
    # Act
    log = apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert log["units"][0]["status"] == "failed"


def test_unreachable_host_keeps_the_source(world):
    # Arrange
    plan, path, proc = world
    # Act
    apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert path.read_bytes() == b"x" * 500


def test_failed_unit_gets_no_pointer(world):
    # Arrange
    plan, path, proc = world
    # Act
    apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert not pointer_path_for(path).exists()


def test_run_log_records_completed_with_failures(world):
    # Arrange
    plan, _path, proc = world
    # Act
    log = apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert json.loads(Path(log["log_path"]).read_text())["status"] == (
        "completed-with-failures"
    )


def test_unit_touched_after_planning_is_skipped(world):
    # Arrange
    plan, path, proc = world
    path.read_bytes()
    os.utime(path, (time.time(), path.stat().st_mtime))
    # Act
    log = apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert (log["units"][0]["status"], log["units"][0]["reason"]) == (
        "skipped",
        "touched since planning (no longer cold)",
    )


def test_unit_removed_after_planning_is_skipped(world):
    # Arrange
    plan, path, proc = world
    path.unlink()
    # Act
    log = apply_tier(plan, runner=_down, proc=proc)
    # Assert
    assert log["units"][0]["status"] == "skipped"


def test_read_pointer_is_none_for_a_path_never_tiered(world):
    # Arrange
    _plan, path, _proc = world
    # Act
    pointer = read_pointer(path)
    # Assert
    assert pointer is None


def test_read_pointer_refuses_a_foreign_json(world):
    # Arrange
    _plan, path, _proc = world
    pointer_path_for(path).write_text(json.dumps({"schema": "something-else"}))
    # Act
    raised = False
    try:
        read_pointer(path)
    except ValueError:
        raised = True
    # Assert
    assert raised


def test_restore_without_a_pointer_is_refused(world):
    # Arrange
    _plan, path, _proc = world
    # Act
    raised = False
    try:
        restore_tiered(path.parent / "never-tiered.dump", runner=_down)
    except FileNotFoundError:
        raised = True
    # Assert
    assert raised


def test_restore_refuses_when_the_original_path_is_occupied(world):
    # Arrange
    _plan, path, _proc = world
    pointer_path_for(path).write_text(json.dumps({"schema": POINTER_SCHEMA}))
    # Act
    raised = False
    try:
        restore_tiered(path, runner=_down)
    except FileExistsError:
        raised = True
    # Assert
    assert raised
