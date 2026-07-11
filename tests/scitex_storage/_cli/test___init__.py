"""Unit tests for the scitex-storage CLI (click CliRunner over real tmp dirs)."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
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


# EOF
