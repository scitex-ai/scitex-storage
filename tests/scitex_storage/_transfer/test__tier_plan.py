"""Tests for ``tier`` unit selection (read-only planning).

Real files with ages set by ``os.utime``; real processes for the holder check
(the ``proc`` seam narrows the table to processes the test owns, as in the
purge tests). ``disk_usage`` is the seam for the target-fill rule, because the
test cannot fill a real filesystem to a chosen percentage.
"""

import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest

from scitex_storage._transfer._tier_plan import plan_tier, pointer_path_for

DAY = 86_400
NOW = time.time()
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def proc(tmp_path):
    """A process table holding only this test process."""
    path = tmp_path / "proc"
    path.mkdir()
    (path / "self").symlink_to(f"/proc/{os.getpid()}")
    return path


@pytest.fixture
def volume(tmp_path):
    root = tmp_path / "volume"
    root.mkdir()
    return root


def _make(path: Path, size: int, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def _age_dirs(root: Path, age_days: float) -> None:
    stamp = NOW - age_days * DAY
    for dirpath, _dirs, _files in os.walk(root):
        os.utime(dirpath, (stamp, stamp))


def _plan(root, proc, **kw):
    kw.setdefault("destination", "scitex-nas-02")
    kw.setdefault("cold_after_days", 30)
    kw.setdefault("min_size_bytes", 100)
    kw.setdefault("disk_usage", lambda _path: Usage(10_000, 9_000, 1_000))
    return plan_tier(root, proc=proc, now=NOW, **kw)


def _paths(plan):
    return sorted(Path(unit.path).name for unit in plan.units)


def test_cold_large_file_is_selected(volume, proc):
    # Arrange
    _make(volume / "old.dump", 500, age_days=100)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == ["old.dump"]


def test_recently_read_file_is_not_selected(volume, proc):
    # Arrange
    path = _make(volume / "read.dump", 500, age_days=100)
    os.utime(path, (NOW - 1 * DAY, NOW - 100 * DAY))
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_small_file_is_not_selected(volume, proc):
    # Arrange
    _make(volume / "tiny.dump", 50, age_days=100)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_cold_directory_moves_as_one_unit(volume, proc):
    # Arrange
    _make(volume / "old" / "a.bin", 80, age_days=200)
    _make(volume / "old" / "b.bin", 80, age_days=200)
    _age_dirs(volume / "old", 200)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert [(Path(u.path).name, u.kind, u.file_count) for u in plan.units] == [
        ("old", "directory", 2)
    ]


def test_directory_with_a_warm_file_is_descended(volume, proc):
    # Arrange
    _make(volume / "mixed" / "cold.bin", 500, age_days=200)
    _make(volume / "mixed" / "warm.bin", 500, age_days=1)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == ["cold.bin"]


def test_excluded_entry_is_never_selected(volume, proc):
    # Arrange
    _make(volume / "keep" / "db.dump", 500, age_days=200)
    _make(volume / "other.dump", 500, age_days=200)
    # Act
    plan = _plan(volume, proc, exclude=("keep",))
    # Assert
    assert _paths(plan) == ["other.dump"]


def test_excluded_entry_keeps_its_parent_from_moving_whole(volume, proc):
    # Arrange
    _make(volume / "proj" / "data.bin", 500, age_days=200)
    _make(volume / "proj" / "secret.key", 500, age_days=200)
    _age_dirs(volume / "proj", 200)
    # Act
    plan = _plan(volume, proc, exclude=("secret.key",))
    # Assert
    assert _paths(plan) == ["data.bin"]


def test_symlink_to_a_cold_file_is_not_followed(volume, proc, tmp_path):
    # Arrange
    outside = _make(tmp_path / "outside" / "big.bin", 500, age_days=200)
    (volume / "link.bin").symlink_to(outside)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_restorable_staging_is_skipped(volume, proc):
    # Arrange
    _make(volume / ".stx-restorable" / "staged.bin", 500, age_days=200)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_pointer_files_are_never_units(volume, proc):
    # Arrange
    _make(volume / ("gone.dump" + ".stx-tiered.json"), 500, age_days=200)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_path_with_an_existing_pointer_is_skipped(volume, proc):
    # Arrange
    path = _make(volume / "again.dump", 500, age_days=200)
    pointer_path_for(path).write_text("{}")
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_hard_linked_file_is_skipped(volume, proc):
    # Arrange
    path = _make(volume / "linked.dump", 500, age_days=200)
    os.link(path, volume / "twin.dump")
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert _paths(plan) == []


def test_order_is_coldest_first_then_largest(volume, proc):
    # Arrange
    _make(volume / "older-small.bin", 200, age_days=300)
    _make(volume / "newer-large.bin", 900, age_days=100)
    _make(volume / "newer-larger.bin", 950, age_days=100)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert [Path(u.path).name for u in plan.units] == [
        "older-small.bin",
        "newer-larger.bin",
        "newer-large.bin",
    ]


def test_target_used_percent_stops_once_reached(volume, proc):
    # Arrange: 90% used of 10_000; each file frees 1_000 (10 points).
    for index in range(4):
        _make(volume / f"f{index}.bin", 1_000, age_days=100 + index)
    # Act
    plan = _plan(volume, proc, target_used_percent=75)
    # Assert
    assert len(plan.units) == 2


def test_target_already_met_selects_nothing(volume, proc):
    # Arrange
    _make(volume / "cold.bin", 1_000, age_days=100)
    # Act
    plan = _plan(volume, proc, target_used_percent=95)
    # Assert
    assert plan.units == []


def test_max_items_caps_the_run(volume, proc):
    # Arrange
    for index in range(3):
        _make(volume / f"f{index}.bin", 500, age_days=100)
    # Act
    plan = _plan(volume, proc, max_items=1)
    # Assert
    assert len(plan.units) == 1


def test_max_bytes_caps_the_run(volume, proc):
    # Arrange
    _make(volume / "big.bin", 900, age_days=200)
    _make(volume / "small.bin", 300, age_days=100)
    # Act
    plan = _plan(volume, proc, max_bytes=500)
    # Assert
    assert _paths(plan) == ["small.bin"]


def test_projected_usage_subtracts_the_selection(volume, proc):
    # Arrange
    _make(volume / "cold.bin", 1_000, age_days=100)
    # Act
    plan = _plan(volume, proc)
    # Assert
    assert plan.projected_used_percent == pytest.approx(80.0)


def test_file_held_open_by_a_process_is_skipped(volume, proc):
    # Arrange
    path = _make(volume / "open.dump", 500, age_days=200)
    code = f"f = open({str(path)!r}); import time; time.sleep(30)"
    child = subprocess.Popen([sys.executable, "-c", code])
    (proc / str(child.pid)).symlink_to(f"/proc/{child.pid}")
    time.sleep(0.5)
    try:
        # Act
        plan = _plan(volume, proc)
    finally:
        child.kill()
        child.wait()
    # Assert
    assert [s["reason"].split(":")[0] for s in plan.skipped] == ["held open"]


def test_unknown_destination_is_refused(volume, proc):
    # Arrange
    raised = None
    # Act
    try:
        _plan(volume, proc, destination="somewhere-else")
    except ValueError as exc:
        raised = exc
    # Assert
    assert raised is not None


def test_remote_path_mirrors_the_absolute_path(volume, proc):
    # Arrange
    path = _make(volume / "old.dump", 500, age_days=100)
    # Act
    plan = _plan(volume, proc, remote_root="/archive")
    # Assert
    assert plan.units[0].remote_path == "/archive/" + str(path.resolve()).lstrip("/")
