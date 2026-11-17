"""Unit tests for the ``scitex-storage backup`` CLI command.

--yes isn't exercised here (it would shell out to real ssh/rsync with no
fake-runner injection point at the CLI layer) -- that coverage lives in
the transfer tests instead. These cover dry-run + validation only.
"""

from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_cli_backup_dry_run_exits_zero(tmp_path):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["backup", str(source), "--to", "scitex-nas-02"])
    # Assert
    assert result.exit_code == 0


def test_cli_backup_requires_to_flag(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["backup", str(tmp_path)])
    # Assert
    assert result.exit_code != 0


def test_cli_backup_dry_run_reports_retained_source(tmp_path):
    # Arrange -- the report must never read as a delete.
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["backup", str(source), "--to", "scitex-nas-02"])
    # Assert
    assert "WOULD ARCHIVE" in result.output
