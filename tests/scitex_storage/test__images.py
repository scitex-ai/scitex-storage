"""Unit tests for scitex_storage._images (versioned-file rotation)."""

import os
import time

import pytest

from scitex_storage._images import (
    ApplyResult,
    PruneCandidate,
    PrunePlan,
    apply_prune,
    plan_prune,
)

_proc_only = pytest.mark.skipif(
    not os.path.isdir("/proc"), reason="in-use guard needs Linux /proc"
)


def _touch(path, size=1, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _dated(tmp_path, name, size=1, age_seconds=0):
    """Create a candidate file whose mtime is ``age_seconds`` in the past."""
    now = time.time()
    return _touch(tmp_path / name, size=size, mtime=now - age_seconds)


def test_plan_prune_raises_for_missing_directory(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_prune(missing, keep=5)


def test_plan_prune_raises_for_non_directory(tmp_path):
    # Arrange
    a_file = _touch(tmp_path / "a.sif")
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        plan_prune(a_file, keep=5)


def test_plan_prune_keeps_newest_n_when_none_referenced(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=300)
    _dated(tmp_path, "a-2.sif", age_seconds=200)
    _dated(tmp_path, "a-3.sif", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=2)
    # Assert
    assert {c.path.name for c in plan.remove} == {"a-1.sif"}


def test_plan_prune_never_removes_a_referenced_file(tmp_path):
    # Arrange — the oldest file is the one currently symlinked "live"
    old = _dated(tmp_path, "a-1.sif", age_seconds=300)
    _dated(tmp_path, "a-2.sif", age_seconds=200)
    _dated(tmp_path, "a-3.sif", age_seconds=100)
    (tmp_path / "a.sif").symlink_to(old)
    # Act
    plan = plan_prune(tmp_path, keep=1)
    # Assert
    assert old not in {c.path for c in plan.remove}


def test_plan_prune_referenced_includes_the_symlinked_file(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=300)
    (tmp_path / "a.sif").symlink_to(old)
    # Act
    plan = plan_prune(tmp_path, keep=0)
    # Assert
    assert {c.path for c in plan.referenced} == {old}


def test_plan_prune_referenced_count_matches_symlink_count(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=300)
    (tmp_path / "a.sif").symlink_to(old)
    # Act
    plan = plan_prune(tmp_path, keep=0)
    # Assert
    assert len(plan.referenced) == 1


def test_plan_prune_keep_budget_excludes_referenced_count(tmp_path):
    # Arrange — keep is a TOTAL target: 1 referenced + keep=2 -> 1 more newest kept
    ref = _dated(tmp_path, "a-1.sif", age_seconds=300)
    (tmp_path / "a.sif").symlink_to(ref)
    _dated(tmp_path, "a-2.sif", age_seconds=200)
    newest = _dated(tmp_path, "a-3.sif", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=2)
    # Assert
    assert {c.path for c in plan.kept} == {ref, newest}


def test_plan_prune_removes_middle_file_when_referenced_consumes_keep_budget(tmp_path):
    # Arrange — same setup: 1 referenced + keep=2 -> the middle file is surplus
    ref = _dated(tmp_path, "a-1.sif", age_seconds=300)
    (tmp_path / "a.sif").symlink_to(ref)
    _dated(tmp_path, "a-2.sif", age_seconds=200)
    _dated(tmp_path, "a-3.sif", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=2)
    # Assert
    assert plan.remove[0].path.name == "a-2.sif"


def test_plan_prune_pattern_filters_candidates(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=100)
    _dated(tmp_path, "notes.txt", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=0, pattern="*.sif")
    # Assert
    names = {c.path.name for c in plan.referenced + plan.kept + plan.remove}
    assert "notes.txt" not in names


def test_plan_prune_does_not_touch_the_filesystem(tmp_path):
    # Arrange
    f = _dated(tmp_path, "a-1.sif", age_seconds=100)
    # Act
    plan_prune(tmp_path, keep=0)
    # Assert
    assert f.exists()


def test_plan_prune_reclaimable_bytes_sums_remove_sizes(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", size=10, age_seconds=200)
    _dated(tmp_path, "a-2.sif", size=20, age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=1)
    # Assert
    assert plan.reclaimable_bytes == 10


def test_plan_prune_symlink_itself_is_never_a_candidate(tmp_path):
    # Arrange
    real = _dated(tmp_path, "a-1.sif", age_seconds=100)
    (tmp_path / "a.sif").symlink_to(real)
    # Act
    plan = plan_prune(tmp_path, keep=0)
    # Assert
    names = {c.path.name for c in plan.referenced + plan.kept + plan.remove}
    assert "a.sif" not in names


def test_apply_prune_removed_reports_the_unlinked_candidate(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    # Act
    result = apply_prune(plan)
    # Assert
    assert [c.path for c in result.removed] == [old]


def test_apply_prune_actually_unlinks_the_old_file(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    # Act
    apply_prune(plan)
    # Assert
    assert not old.exists()


def test_apply_prune_leaves_the_kept_file_untouched(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=200)
    newest = _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    # Act
    apply_prune(plan)
    # Assert
    assert newest.exists()


def test_apply_prune_returns_empty_removed_when_nothing_to_remove(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=5)
    # Act
    result = apply_prune(plan)
    # Assert
    assert result.removed == []


def test_apply_prune_returns_an_applyresult_instance(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=200)
    plan = plan_prune(tmp_path, keep=0)
    # Act
    result = apply_prune(plan)
    # Assert
    assert isinstance(result, ApplyResult)


def test_apply_prune_reclaimed_bytes_sums_actually_removed(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", size=10, age_seconds=200)
    _dated(tmp_path, "a-2.sif", size=20, age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    # Act
    result = apply_prune(plan)
    # Assert
    assert result.reclaimed_bytes == 10


@_proc_only
def test_apply_prune_does_not_unlink_a_candidate_that_is_open(tmp_path):
    # Arrange — hold an open handle to simulate a live mmap'd/open file
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    with open(old, "rb"):
        # Act
        result = apply_prune(plan)
    # Assert
    assert result.removed == []


@_proc_only
def test_apply_prune_leaves_an_in_use_candidate_on_disk(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    with open(old, "rb"):
        # Act
        apply_prune(plan)
    # Assert
    assert old.exists()


@_proc_only
def test_apply_prune_reports_an_in_use_candidate_as_skipped(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    with open(old, "rb"):
        # Act
        result = apply_prune(plan)
    # Assert
    assert result.skipped_in_use[0].candidate.path == old


@_proc_only
def test_apply_prune_skipped_in_use_reports_the_holding_pid(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    with open(old, "rb"):
        # Act
        result = apply_prune(plan)
    # Assert
    assert os.getpid() in result.skipped_in_use[0].pids


def test_apply_prune_raises_if_target_became_a_symlink_since_planning(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    plan = plan_prune(tmp_path, keep=1)
    old.unlink()
    elsewhere = _touch(tmp_path / "elsewhere.sif")
    old.symlink_to(elsewhere)
    # Act
    # Assert
    with pytest.raises(FileExistsError):
        apply_prune(plan)


def test_prune_candidate_is_a_dataclass_instance(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=5)
    # Assert
    assert isinstance(plan.kept[0], PruneCandidate)


def test_plan_prune_returns_a_pruneplan_instance(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=100)
    # Act
    plan = plan_prune(tmp_path, keep=5)
    # Assert
    assert isinstance(plan, PrunePlan)


# EOF
