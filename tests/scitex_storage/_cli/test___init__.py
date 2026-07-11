"""Unit tests for the scitex-storage CLI (click CliRunner over real tmp dirs)."""

import json
import os
import tempfile
import time
from pathlib import Path

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
def home_with_scitex():
    """Real temp HOME containing a populated ~/.scitex (and no ~/proj).

    Sets ``$HOME`` for the duration of the test and restores it on
    teardown — the sanctioned env-var pattern (no ``monkeypatch``).
    """
    prev = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as td:
        os.environ["HOME"] = td
        state = Path(td) / ".scitex" / "state"
        state.mkdir(parents=True)
        (state / "x.bin").write_bytes(b"\0" * 10)
        try:
            yield Path(td)
        finally:
            if prev is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev


def test_cli_scan_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


def test_cli_scan_prints_root(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert str(tmp_path.resolve()) in result.output


def test_cli_scan_json_flag_emits_valid_json(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--json"])
    # Assert
    assert len(json.loads(result.output)["roots"][0]["children"]) == 1


def test_cli_scan_sort_files_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--sort", "files"])
    # Assert
    assert result.exit_code == 0


def test_cli_scan_rejects_missing_path():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", "/no/such/path/at/all"])
    # Assert
    assert result.exit_code != 0


def test_cli_scan_no_args_scans_default_roots(home_with_scitex):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan"])
    # Assert
    assert result.exit_code == 0


def test_cli_version_flag_long_form():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--version"])
    # Assert
    assert result.exit_code == 0


def test_cli_version_flag_short_form():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["-V"])
    # Assert
    assert result.exit_code == 0


def test_cli_help_recursive_flag():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--help-recursive"])
    # Assert
    assert result.exit_code == 0


def test_cli_root_json_flag_is_accepted():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--json"])
    # Assert
    assert result.exit_code == 0


def test_cli_no_args_shows_help():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, [])
    # Assert
    assert "scan" in result.output


def _dated(tmp_path, name, size=1, age_seconds=0):
    now = time.time()
    return _touch(tmp_path / name, size=size, mtime=now - age_seconds)


def test_cli_images_prune_exits_zero(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["images", "prune", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


def test_cli_images_prune_defaults_to_dry_run(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["images", "prune", str(tmp_path), "--keep", "1"])
    # Assert
    assert old.exists()


def test_cli_images_prune_apply_flag_deletes(tmp_path):
    # Arrange
    old = _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["images", "prune", str(tmp_path), "--keep", "1", "--apply"])
    # Assert
    assert not old.exists()


def test_cli_images_prune_apply_never_deletes_referenced(tmp_path):
    # Arrange
    live = _dated(tmp_path, "a-1.sif", age_seconds=200)
    (tmp_path / "a.sif").symlink_to(live)
    runner = CliRunner()
    # Act
    runner.invoke(main, ["images", "prune", str(tmp_path), "--keep", "0", "--apply"])
    # Assert
    assert live.exists()


def test_cli_images_prune_json_flag_reports_applied_false_on_dry_run(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["images", "prune", str(tmp_path), "--keep", "1", "--json"]
    )
    # Assert
    assert json.loads(result.output)["applied"] is False


def test_cli_images_prune_json_flag_reports_the_remove_set(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", age_seconds=200)
    _dated(tmp_path, "a-2.sif", age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["images", "prune", str(tmp_path), "--keep", "1", "--json"]
    )
    # Assert
    assert len(json.loads(result.output)["remove"]) == 1


def test_cli_images_prune_rejects_missing_directory():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["images", "prune", "/no/such/path/at/all"])
    # Assert
    assert result.exit_code != 0


def test_cli_images_prune_pattern_option_is_honoured(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.tar", age_seconds=200)
    _dated(tmp_path, "a-2.tar", age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        ["images", "prune", str(tmp_path), "--keep", "1", "--pattern", "*.tar", "--json"],
    )
    # Assert
    payload = json.loads(result.output)
    assert len(payload["remove"]) == 1


def test_cli_images_prune_apply_json_reports_removed_count(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", size=10, age_seconds=200)
    _dated(tmp_path, "a-2.sif", size=20, age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["images", "prune", str(tmp_path), "--keep", "1", "--apply", "--json"]
    )
    # Assert
    assert len(json.loads(result.output)["removed"]) == 1


def test_cli_images_prune_apply_json_reports_reclaimed_bytes(tmp_path):
    # Arrange
    _dated(tmp_path, "a-1.sif", size=10, age_seconds=200)
    _dated(tmp_path, "a-2.sif", size=20, age_seconds=100)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["images", "prune", str(tmp_path), "--keep", "1", "--apply", "--json"]
    )
    # Assert
    assert json.loads(result.output)["reclaimed_bytes"] == 10


# EOF
