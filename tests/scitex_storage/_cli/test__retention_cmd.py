"""Unit tests for the ``scitex-storage sweep-restorable`` CLI command."""

import json
import os

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main
from scitex_storage._transfer._retention import stage_for_retention


@pytest.fixture
def sandbox_home(tmp_path):
    """Point $HOME at tmp_path; restore afterwards. No mocks involved."""
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def _staged(tmp_path, name="work", retention_days=0.0):
    src = tmp_path / name
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.bin").write_bytes(b"x" * 64)
    return stage_for_retention(src, retention_days=retention_days)


def test_cli_sweep_restorable_defaults_to_dry_run(sandbox_home, tmp_path):
    # Arrange
    record = _staged(tmp_path)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-restorable"])
    # Assert -- reported, not removed.
    from pathlib import Path

    outcome = (
        result.exit_code,
        "expired (dry-run)" in result.output,
        Path(record.staged_path).is_dir(),
    )
    assert outcome == (0, True, True)


def test_cli_sweep_restorable_yes_deletes_expired(sandbox_home, tmp_path):
    # Arrange
    record = _staged(tmp_path)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-restorable", "--yes"])
    # Assert
    from pathlib import Path

    outcome = (
        result.exit_code,
        "removed" in result.output,
        Path(record.staged_path).exists(),
    )
    assert outcome == (0, True, False)


def test_cli_sweep_restorable_retains_unexpired(sandbox_home, tmp_path):
    # Arrange -- default 7-day retention: nothing is due.
    record = _staged(tmp_path, retention_days=7.0)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-restorable", "--yes"])
    # Assert
    from pathlib import Path

    outcome = (
        result.exit_code,
        "retained" in result.output,
        Path(record.staged_path).is_dir(),
    )
    assert outcome == (0, True, True)


def test_cli_sweep_restorable_json_reports_counts(sandbox_home, tmp_path):
    # Arrange
    _staged(tmp_path, name="old", retention_days=0.0)
    _staged(tmp_path, name="new", retention_days=7.0)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-restorable", "--json"])
    payload = json.loads(result.output)
    # Assert
    outcome = (result.exit_code, len(payload["removed"]), len(payload["retained"]))
    assert outcome == (0, 1, 1)


def test_explicit_dry_run_wins_over_yes_flag(sandbox_home, tmp_path):
    # Arrange
    from pathlib import Path

    record = _staged(tmp_path)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["sweep-restorable", "--yes", "--dry-run"])
    # Assert
    assert (
        result.exit_code,
        Path(record.staged_path).exists(),
        "expired (dry-run)" in result.output,
    ) == (0, True, True)


def test_cli_byte_budget_keeps_expired_staging(sandbox_home, tmp_path):
    # Arrange
    from pathlib import Path

    record = _staged(tmp_path)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["sweep-restorable", "--yes", "--max-bytes", "1", "--json"]
    )
    payload = json.loads(result.output)
    # Assert
    assert (
        result.exit_code,
        payload["removed"],
        payload["retained"],
        Path(record.staged_path).exists(),
    ) == (0, [], [record.staged_path], True)
