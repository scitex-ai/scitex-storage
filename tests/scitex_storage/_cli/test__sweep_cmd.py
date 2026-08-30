"""Unit tests for the ``scitex-storage sweep`` / ``sweep-status`` CLI commands."""

import json
import os
import time

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size=1, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


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
    """Unset $SLURM_JOB_ID for the duration of the test, restore on teardown.

    Don't rely on the ambient environment lacking SLURM_JOB_ID -- CI
    runners can (and now do, via the spartan-cpu-org-* self-hosted
    runners that live inside a long-held SLURM allocation) have it set.
    """
    prev = os.environ.pop("SLURM_JOB_ID", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["SLURM_JOB_ID"] = prev


def _hog(tmp_path, name, n_files=10, age_seconds=2 * 24 * 3600):
    now = time.time()
    mtime = now - age_seconds
    d = tmp_path / name
    for i in range(n_files):
        _touch(d / f"f{i}.bin", size=1, mtime=mtime)
    return d


@pytest.mark.requires_fd
def test_cli_sweep_exits_zero(tmp_path):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep", str(tmp_path), "--threshold-files", "10"])
    # Assert
    assert result.exit_code == 0


def test_cli_sweep_requires_threshold_files():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep", "/tmp"])
    # Assert
    assert result.exit_code != 0


def test_cli_sweep_defaults_to_dry_run(tmp_path):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["sweep", str(tmp_path), "--threshold-files", "10"])
    # Assert
    assert hog.exists()


def test_cli_sweep_apply_without_confirm_is_rejected(tmp_path, slurm_job):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["sweep", str(tmp_path), "--threshold-files", "10", "--apply"]
    )
    # Assert
    assert result.exit_code != 0


@pytest.mark.requires_fd
def test_cli_sweep_apply_with_confirm_removes_the_directory(tmp_path, slurm_job):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        [
            "sweep",
            str(tmp_path),
            "--threshold-files",
            "10",
            "--apply",
            "--confirm",
            "hog",
        ],
    )
    # Assert
    assert not hog.exists()


def test_cli_sweep_apply_without_slurm_job_id_fails(tmp_path, no_slurm_job):
    # Arrange
    hog = _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    runner.invoke(
        main,
        [
            "sweep",
            str(tmp_path),
            "--threshold-files",
            "10",
            "--apply",
            "--confirm",
            "hog",
        ],
    )
    # Assert
    assert hog.exists()


@pytest.mark.requires_fd
def test_cli_sweep_json_flag_reports_candidates(tmp_path):
    # Arrange
    _hog(tmp_path, "hog", n_files=20)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["sweep", str(tmp_path), "--threshold-files", "10", "--json"]
    )
    # Assert
    assert len(json.loads(result.output)["candidates"]) == 1


@pytest.mark.requires_fd
def test_cli_sweep_min_age_hours_excludes_fresh_candidate(tmp_path):
    # Arrange
    _hog(tmp_path, "fresh", n_files=20, age_seconds=60)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["sweep", str(tmp_path), "--threshold-files", "10", "--min-age-hours", "1", "--json"],
    )
    # Assert
    assert json.loads(result.output)["candidates"] == []


def test_cli_sweep_rejects_missing_directory():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep", "/no/such/path", "--threshold-files", "10"])
    # Assert
    assert result.exit_code != 0


def test_cli_sweep_status_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-status", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


def test_cli_sweep_status_json_reports_swept_entries(tmp_path):
    # Arrange
    _touch(tmp_path / "hog.tar")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-status", str(tmp_path), "--json"])
    # Assert
    assert len(json.loads(result.output)["swept"]) == 1


# EOF
