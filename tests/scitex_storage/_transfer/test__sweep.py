"""Unit tests for scitex_storage._transfer._sweep (inode-aware tar-in-place rotation)."""

import os
import time

import pytest

from scitex_storage._transfer._sweep import (
    InsufficientSpaceError,
    SweepCandidate,
    SweepPlan,
    SweepResult,
    SweptEntry,
    _parse_slurm_remaining,
    _sweep_one,
    apply_sweep,
    check_space,
    free_bytes,
    plan_sweep,
    sweep_status,
)


def _touch(path, size=1, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _hog(tmp_path, name, n_files=10, age_seconds=2 * 24 * 3600):
    """Create a subdirectory with n_files files, all aged ``age_seconds``."""
    now = time.time()
    mtime = now - age_seconds
    d = tmp_path / name
    for i in range(n_files):
        _touch(d / f"f{i}.bin", size=1, mtime=mtime)
    return d


@pytest.fixture
def slurm_job():
    """Set $SLURM_JOB_ID for the duration of the test, restore on teardown."""
    prev = os.environ.get("SLURM_JOB_ID")
    os.environ["SLURM_JOB_ID"] = "999999"
    try:
        yield "999999"
    finally:
        if prev is None:
            os.environ.pop("SLURM_JOB_ID", None)
        else:
            os.environ["SLURM_JOB_ID"] = prev


@pytest.fixture
def no_slurm_job():
    """Ensure $SLURM_JOB_ID is unset for the duration of the test."""
    prev = os.environ.get("SLURM_JOB_ID")
    os.environ.pop("SLURM_JOB_ID", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["SLURM_JOB_ID"] = prev


# --- plan_sweep -------------------------------------------------------------


def test_plan_sweep_raises_for_missing_directory(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_sweep(missing, threshold_files=10)


def test_plan_sweep_includes_child_meeting_threshold(tmp_path):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Assert
    assert {c.name for c in plan.candidates} == {"hog"}


def test_plan_sweep_excludes_child_below_threshold(tmp_path):
    # Arrange
    _hog(tmp_path, "small", n_files=5)
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Assert
    assert plan.candidates == []


def test_plan_sweep_excludes_fresh_child_from_candidates(tmp_path):
    # Arrange
    _hog(tmp_path, "fresh", n_files=20, age_seconds=60)  # 1 minute old
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10, min_age_seconds=3600)
    # Assert
    assert plan.candidates == []


def test_plan_sweep_reports_fresh_child_as_skipped(tmp_path):
    # Arrange
    _hog(tmp_path, "fresh", n_files=20, age_seconds=60)
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10, min_age_seconds=3600)
    # Assert
    assert {c.name for c in plan.skipped_fresh} == {"fresh"}


def test_plan_sweep_reclaimable_inodes_sums_file_count_minus_one(tmp_path):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Assert
    assert plan.reclaimable_inodes == 19


def test_plan_sweep_does_not_touch_the_filesystem(tmp_path):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    # Act
    plan_sweep(tmp_path, threshold_files=10)
    # Assert
    assert hog.exists()


def test_plan_sweep_returns_a_sweepplan_instance(tmp_path):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    # Act
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Assert
    assert isinstance(plan, SweepPlan)


# --- apply_sweep: SLURM guard -----------------------------------------------


def test_apply_sweep_raises_without_slurm_job_id(tmp_path, no_slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_sweep(plan, confirm_names=["hog"])


# --- apply_sweep: consent gate -----------------------------------------------


def test_apply_sweep_raises_for_unknown_confirm_name(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    # Assert
    with pytest.raises(ValueError):
        apply_sweep(plan, confirm_names=["typo-name"])


def test_apply_sweep_leaves_unconfirmed_candidate_untouched(tmp_path, slurm_job):
    # Arrange
    hog_a = _hog(tmp_path, "hog-a", n_files=20)
    _hog(tmp_path, "hog-b", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    apply_sweep(plan, confirm_names=["hog-b"])
    # Assert
    assert hog_a.exists()


# --- apply_sweep: happy path --------------------------------------------------


def test_apply_sweep_removes_the_original_directory(tmp_path, slurm_job):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert not hog.exists()


def test_apply_sweep_creates_the_tar(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert (tmp_path / "hog.tar").exists()


def test_apply_sweep_reports_swept_candidate(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    result = apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert [s.candidate.name for s in result.swept] == ["hog"]


def test_apply_sweep_member_count_matches_original_file_count(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    result = apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert result.swept[0].member_count == 20


def test_apply_sweep_reclaimed_inodes_is_member_count_minus_one(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    result = apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert result.reclaimed_inodes == 19


def test_apply_sweep_returns_a_sweepresult_instance(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    result = apply_sweep(plan, confirm_names=["hog"])
    # Assert
    assert isinstance(result, SweepResult)


def test_apply_sweep_never_dereferences_a_symlinked_subdirectory(tmp_path, slurm_job):
    # Arrange
    outside = tmp_path / "outside"
    _touch(outside / "secret.bin", size=1)
    hog = _hog(tmp_path, "hog", n_files=20)
    (hog / "escape").symlink_to(outside, target_is_directory=True)
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    result = apply_sweep(plan, confirm_names=["hog"])
    # Assert -- escape/ contributes 0 to the count, matching scan()'s doctrine
    assert result.swept[0].member_count == 20


def test_apply_sweep_raises_if_tar_path_already_exists(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    _touch(tmp_path / "hog.tar")
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    # Assert
    with pytest.raises(FileExistsError):
        apply_sweep(plan, confirm_names=["hog"])


def test_apply_sweep_does_not_remove_original_when_tar_path_collides(tmp_path, slurm_job):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    _touch(tmp_path / "hog.tar")
    plan = plan_sweep(tmp_path, threshold_files=10)
    # Act
    try:
        apply_sweep(plan, confirm_names=["hog"])
    except FileExistsError:
        pass
    # Assert
    assert hog.exists()


# --- apply_sweep: walltime-aware stopping ------------------------------------


def test_apply_sweep_stops_before_a_candidate_it_cannot_finish(tmp_path, slurm_job):
    # Arrange -- 60s left, needs 300s min -- must stop before starting
    os.environ["SLURM_JOB_END_TIME"] = str(time.time() + 60)
    try:
        _hog(tmp_path, "hog", n_files=20)
        plan = plan_sweep(tmp_path, threshold_files=10)
        # Act
        result = apply_sweep(plan, confirm_names=["hog"], min_remaining_seconds=300.0)
    finally:
        os.environ.pop("SLURM_JOB_END_TIME", None)
    # Assert
    assert result.swept == []


def test_apply_sweep_reports_stopped_candidate_when_walltime_is_low(tmp_path, slurm_job):
    # Arrange
    os.environ["SLURM_JOB_END_TIME"] = str(time.time() + 60)
    try:
        _hog(tmp_path, "hog", n_files=20)
        plan = plan_sweep(tmp_path, threshold_files=10)
        # Act
        result = apply_sweep(plan, confirm_names=["hog"], min_remaining_seconds=300.0)
    finally:
        os.environ.pop("SLURM_JOB_END_TIME", None)
    # Assert
    assert [c.name for c in result.stopped_low_walltime] == ["hog"]


def test_apply_sweep_proceeds_when_plenty_of_walltime_remains(tmp_path, slurm_job):
    # Arrange
    os.environ["SLURM_JOB_END_TIME"] = str(time.time() + 3600)
    try:
        hog = _hog(tmp_path, "hog", n_files=20)
        plan = plan_sweep(tmp_path, threshold_files=10)
        # Act
        apply_sweep(plan, confirm_names=["hog"], min_remaining_seconds=300.0)
    finally:
        os.environ.pop("SLURM_JOB_END_TIME", None)
    # Assert
    assert not hog.exists()


# --- _parse_slurm_remaining ---------------------------------------------------
# Tested directly: the squeue subprocess path can't be exercised in CI (no
# real SLURM binary), so this is the only way to cover the format parsing.


def test_parse_slurm_remaining_hms():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("01:30:00")
    # Assert
    assert seconds == 5400.0


def test_parse_slurm_remaining_days_hms():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("2-05:00:00")
    # Assert
    assert seconds == 2 * 86400 + 5 * 3600


def test_parse_slurm_remaining_ms():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("05:00")
    # Assert
    assert seconds == 300.0


def test_parse_slurm_remaining_unlimited_is_unknown():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("UNLIMITED")
    # Assert
    assert seconds is None


def test_parse_slurm_remaining_invalid_is_unknown():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("INVALID")
    # Assert
    assert seconds is None


def test_parse_slurm_remaining_garbage_is_unknown():
    # Arrange
    # Act
    seconds = _parse_slurm_remaining("not-a-time")
    # Assert
    assert seconds is None


# --- sweep_status -------------------------------------------------------------


def test_sweep_status_raises_for_missing_directory(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        sweep_status(missing)


def test_sweep_status_finds_a_swept_tar(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    # Act
    entries = sweep_status(tmp_path)
    # Assert
    assert {e.name for e in entries} == {"hog"}


def test_sweep_status_ignores_non_tar_files(tmp_path):
    # Arrange
    _touch(tmp_path / "notes.txt")
    # Act
    entries = sweep_status(tmp_path)
    # Assert
    assert entries == []


def test_sweep_status_flags_original_still_present_as_anomaly(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    (tmp_path / "hog").mkdir()
    # Act
    entries = sweep_status(tmp_path)
    # Assert
    assert entries[0].original_still_present is True


def test_sweep_status_normal_case_reports_no_anomaly(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    # Act
    entries = sweep_status(tmp_path)
    # Assert
    assert entries[0].original_still_present is False


def test_sweep_status_returns_sweptentry_instances(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    # Act
    entries = sweep_status(tmp_path)
    # Assert
    assert isinstance(entries[0], SweptEntry)


# --------------------------------------------------------------------------
# Free-space preflight. `sweep` writes its tar BESIDE the source, i.e. onto
# the very filesystem it was invoked to relieve. Without this gate the verb
# is inverted -- most dangerous exactly where it is most needed. These cases
# are the ones a small fixture on a roomy disk cannot see, so they drive
# check_space directly with the numbers a full disk produces.
# --------------------------------------------------------------------------


def test_an_artifact_larger_than_free_space_is_refused():
    # Arrange -- the real shape: a 187 GiB tar onto 2.3 GB free.
    needed, available = 200_000_000_000, 2_300_000_000

    # Act
    verdict = check_space(needed, available)

    # Assert
    assert verdict.ok is False


def test_the_refusal_names_the_shortfall():
    # Arrange -- "not enough space" is not actionable; the gap is.
    needed, available = 200_000_000_000, 2_300_000_000

    # Act
    verdict = check_space(needed, available)

    # Assert
    assert "short by" in verdict.detail


def test_an_artifact_that_fits_with_headroom_is_allowed():
    # Arrange
    needed, available = 1_000_000_000, 500_000_000_000

    # Act
    verdict = check_space(needed, available)

    # Assert
    assert verdict.ok is True


def test_an_exact_fit_is_refused_because_zero_free_breaks_other_writers():
    # Arrange -- filling a filesystem to 0 bytes breaks every other writer
    # on it, including the card board every agent writes to.
    needed = 1_000_000

    # Act
    verdict = check_space(needed, needed)

    # Assert
    assert verdict.ok is False


def test_an_unknown_destination_size_is_not_an_optimistic_pass():
    # Arrange -- could-not-look must not read as "room available".
    needed = 10

    # Act
    verdict = check_space(needed, None)

    # Assert
    assert verdict.ok is None


def test_an_unknown_artifact_size_is_not_an_optimistic_pass():
    # Arrange
    available = 10_000_000_000

    # Act
    verdict = check_space(None, available)

    # Assert
    assert verdict.ok is None


def test_free_bytes_reads_a_real_filesystem(tmp_path):
    # Arrange -- a real statvfs, no mocks.
    # Act
    got = free_bytes(tmp_path)

    # Assert
    assert got is not None and got > 0


def test_free_bytes_on_a_missing_path_is_unknown_not_zero(tmp_path):
    # Arrange -- zero would mean "measured: full"; None means "not measured".
    missing = tmp_path / "no-such-dir"

    # Act
    got = free_bytes(missing)

    # Assert
    assert got is None


# --- the gate in the REAL sweep path, not just the pure helper ------------
# A gate proven once by hand regresses silently. These drive _sweep_one
# itself with a candidate declared larger than the whole filesystem, which
# is how a full disk looks to it, using real files and a real statvfs.


def _oversized_candidate(tmp_path):
    src = tmp_path / "bigdir"
    src.mkdir()
    (src / "f").write_bytes(b"x" * 100)
    return SweepCandidate(
        name="bigdir",
        path=src,
        file_count=1,
        size=free_bytes(tmp_path) * 10,  # cannot possibly fit
        newest_mtime=0.0,
    )


def test_sweep_refuses_a_candidate_that_cannot_fit(tmp_path):
    # Arrange
    candidate = _oversized_candidate(tmp_path)

    # Act
    raised = pytest.raises(InsufficientSpaceError)

    # Assert
    with raised:
        _sweep_one(candidate)


def test_a_refused_sweep_leaves_the_source_intact(tmp_path):
    # Arrange
    candidate = _oversized_candidate(tmp_path)

    # Act -- the raise IS the action here; that it raises at all is asserted
    # by the test above, so swallowing it keeps this to one assertion.
    try:
        _sweep_one(candidate)
    except InsufficientSpaceError:
        pass

    # Assert
    assert candidate.path.exists()


def test_a_refused_sweep_writes_no_partial_tar(tmp_path):
    # Arrange -- the v1 failure mode consumed the last free space AND left
    # a half-written .sweeping file behind.
    candidate = _oversized_candidate(tmp_path)

    # Act
    try:
        _sweep_one(candidate)
    except InsufficientSpaceError:
        pass

    # Assert
    assert not list(tmp_path.glob("*.tar*"))

# EOF
