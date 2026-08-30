"""Unit tests for scitex_storage._measure._inodes (statvfs-backed inode capacity probe).

NO MOCKS, and the module is shaped so none are needed (PA-306). The
decision layer (`usage_from_counts`) is pure, so every interesting case --
including the ones that are hard to manufacture on a real disk, like a
filesystem with no inode table -- is exercised with plain integers. The
I/O layer (`probe`) is exercised against real paths: a real directory and
a really-missing one.

The tests that matter most are the ones pinning the THREE-STATE verdict,
not the happy path. Both non-measured states are what a naive probe gets
wrong, silently and in the direction of false reassurance:

* a filesystem with no fixed inode table (btrfs/ZFS, f_files=0) must NOT
  render as "0% used"
* an unreadable path must NOT render as "0% used" either
"""

import os
from pathlib import Path

import pytest

from scitex_storage._measure._inodes import (
    COULD_NOT_LOOK,
    MEASURED,
    NOT_APPLICABLE,
    InodeUsage,
    probe,
    probe_paths,
    usage_from_counts,
)

# --------------------------------------------------------------------------
# usage_from_counts -- the pure decision layer.
# --------------------------------------------------------------------------


def test_usage_from_counts_reports_measured_for_a_real_inode_table():
    # Arrange
    total, free = 1000, 750
    # Act
    usage = usage_from_counts("/x", total=total, free=free)
    # Assert
    assert usage.verdict == MEASURED


def test_usage_from_counts_derives_used_from_total_minus_free():
    # Arrange
    total, free = 1000, 750
    # Act
    usage = usage_from_counts("/x", total=total, free=free)
    # Assert
    assert usage.used == 250


def test_usage_from_counts_computes_percent_used():
    # Arrange
    total, free = 1000, 750
    # Act
    usage = usage_from_counts("/x", total=total, free=free)
    # Assert
    assert usage.percent_used == 25.0


def test_usage_from_counts_reports_the_real_punim0264_figure():
    # Arrange -- the live measurement this verb was built for
    # (Spartan punim0264, 2026-07-17), cross-checked against that host's
    # own check_project_usage to within 3 inodes.
    # Act
    usage = usage_from_counts("/data/gpfs/projects/punim0264", 7_000_000, 268_927)
    # Assert
    assert round(usage.percent_used, 1) == 96.2


def test_usage_from_counts_reports_not_applicable_when_there_is_no_inode_table():
    # Arrange -- btrfs/ZFS report f_files=0 (inodes allocated on demand).
    total, free = 0, 0
    # Act
    usage = usage_from_counts("/x", total=total, free=free)
    # Assert
    assert usage.verdict == NOT_APPLICABLE


def test_usage_from_counts_never_reports_zero_percent_for_a_dynamic_filesystem():
    # Arrange -- the regression this module exists to prevent: 0 of 0
    # inodes must not become a reassuring "0% used".
    # Act
    usage = usage_from_counts("/x", total=0, free=0)
    # Assert
    assert usage.percent_used is None


def test_usage_from_counts_explains_why_a_dynamic_filesystem_is_not_applicable():
    # Arrange -- btrfs/ZFS.
    total, free = 0, 0
    # Act
    usage = usage_from_counts("/x", total=total, free=free)
    # Assert -- an operator must not have to guess what the verdict meant.
    assert "no fixed inode table" in (usage.detail or "")


def test_usage_from_counts_reports_a_full_filesystem_as_one_hundred_percent():
    # Arrange -- zero free inodes: every write fails from here.
    # Act
    usage = usage_from_counts("/x", total=1000, free=0)
    # Assert
    assert usage.percent_used == 100.0


def test_usage_from_counts_carries_the_mount_annotation_through():
    # Arrange
    mount, fstype = "/data", "gpfs"
    # Act
    usage = usage_from_counts("/x", 1000, 750, mount=mount, fstype=fstype)
    # Assert
    assert (usage.mount, usage.fstype) == ("/data", "gpfs")


# --------------------------------------------------------------------------
# is_critical / exceeds -- the alarm predicate.
# --------------------------------------------------------------------------


def test_is_critical_is_true_at_the_default_threshold():
    # Arrange -- 90% used, exactly at the default.
    # Act
    usage = usage_from_counts("/x", total=1000, free=100)
    # Assert
    assert usage.is_critical is True


def test_is_critical_is_false_below_the_default_threshold():
    # Arrange -- 89%, the figure punim0264 sat at a week before it hit 95%+.
    # Act
    usage = usage_from_counts("/x", total=1000, free=110)
    # Assert
    assert usage.is_critical is False


def test_is_critical_is_false_for_a_dynamic_filesystem():
    # Arrange -- NOT_APPLICABLE must never fire the alarm.
    # Act
    usage = usage_from_counts("/x", total=0, free=0)
    # Assert
    assert usage.is_critical is False


def test_is_critical_is_false_for_an_unmeasured_path():
    # Arrange -- an unknown is not an alarm; it is its own case, and
    # callers must reach for the verdict rather than infer safety here.
    # Act
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK)
    # Assert
    assert usage.is_critical is False


def test_exceeds_is_true_above_a_custom_threshold():
    # Arrange -- 50% used.
    # Act
    usage = usage_from_counts("/x", total=1000, free=500)
    # Assert
    assert usage.exceeds(40.0) is True


def test_exceeds_is_false_below_a_custom_threshold():
    # Arrange -- 50% used.
    # Act
    usage = usage_from_counts("/x", total=1000, free=500)
    # Assert
    assert usage.exceeds(60.0) is False


def test_exceeds_is_false_for_an_unmeasured_path_at_any_threshold():
    # Arrange -- even a 0% threshold must not fire on a path never read.
    # Act
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK)
    # Assert
    assert usage.exceeds(0.0) is False


def test_unmeasured_usage_has_no_numeric_zero_defaults():
    # Arrange -- guards the dataclass contract itself: a caller that
    # forgets to check the verdict must not silently read a 0.
    # Act
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK)
    # Assert
    assert (usage.total, usage.used, usage.free, usage.percent_used) == (
        None,
        None,
        None,
        None,
    )


# --------------------------------------------------------------------------
# probe -- the I/O layer, against real paths.
# --------------------------------------------------------------------------


def test_probe_measures_a_real_directory(tmp_path):
    # Arrange -- tmp_path is on whatever the test host uses; a real inode
    # table and a dynamic one are both legitimate answers, and neither is
    # a failure.
    # Act
    usage = probe(tmp_path)
    # Assert
    assert usage.verdict in (MEASURED, NOT_APPLICABLE)


def test_probe_agrees_with_statvfs_on_a_real_directory(tmp_path):
    # Arrange -- the probe must report what the kernel reports, not a
    # derived approximation of it.
    st = os.statvfs(tmp_path)
    # Act
    usage = probe(tmp_path)
    # Assert
    assert usage.total == (st.f_files or None)


def test_probe_reports_could_not_look_for_a_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    usage = probe(missing)
    # Assert
    assert usage.verdict == COULD_NOT_LOOK


def test_probe_does_not_raise_for_a_missing_path(tmp_path):
    # Arrange -- a probe reporting on a broken system must not itself break.
    missing = tmp_path / "definitely-not-here"
    # Act
    usage = probe(missing)
    # Assert
    assert usage.percent_used is None


def test_probe_explains_why_it_could_not_look(tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    usage = probe(missing)
    # Assert -- an operator must be able to act on this without a debugger.
    assert "FileNotFoundError" in (usage.detail or "")


@pytest.mark.skipif(
    not Path("/proc/self/mountinfo").exists(),
    reason="no /proc/self/mountinfo on this platform",
)
def test_probe_annotates_the_backing_mount_when_the_platform_exposes_one(tmp_path):
    # Arrange -- Linux only (guarded above); elsewhere the annotation is
    # legitimately absent and the measurement still stands on statvfs alone.
    target = tmp_path
    # Act
    usage = probe(target)
    # Assert
    assert usage.mount is not None


def test_probe_expands_a_user_relative_path():
    # Arrange
    expected = os.path.expanduser("~")
    # Act
    usage = probe("~")
    # Assert
    assert str(usage.path) == expected


def test_probe_paths_returns_one_result_per_input_in_order(tmp_path):
    # Arrange
    a = tmp_path / "a"
    a.mkdir()
    missing = tmp_path / "nope"
    # Act
    results = probe_paths([a, missing])
    # Assert
    assert [str(r.path) for r in results] == [str(a), str(missing)]


def test_probe_paths_does_not_collapse_two_paths_on_the_same_mount(tmp_path):
    # Arrange -- both live on the same filesystem.
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    # Act
    results = probe_paths([a, b])
    # Assert -- the output must keep corresponding to the question asked.
    assert len(results) == 2


def test_probe_paths_keeps_a_readable_and_an_unreadable_path_distinct(tmp_path):
    # Arrange -- one real, one missing.
    a = tmp_path / "a"
    a.mkdir()
    missing = tmp_path / "nope"
    # Act
    verdicts = [r.verdict for r in probe_paths([a, missing])]
    # Assert
    assert verdicts[1] == COULD_NOT_LOOK


# EOF
