"""Unit tests for the scitex-storage CLI (click CliRunner, no real fs)."""

import json

from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_cli_scan_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


def test_cli_scan_prints_root(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert str(tmp_path) in result.output


def test_cli_scan_json_flag_emits_valid_json(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--json"])
    # Assert
    assert json.loads(result.output)["files_scanned"] == 1


def test_cli_scan_rejects_missing_path():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", "/no/such/path/at/all"])
    # Assert
    assert result.exit_code != 0


def test_cli_scan_no_dedupe_flag_skips_duplicates(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"x" * 50)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--no-dedupe", "--json"])
    # Assert
    assert json.loads(result.output)["duplicate_groups"] == []


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
