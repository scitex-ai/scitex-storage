"""Unit tests for the ``scitex-storage archive`` / ``restore`` CLI commands.

--yes isn't exercised here (it would shell out to real ssh/rsync with no
fake-runner injection point at the CLI layer) -- that coverage lives in
tests/scitex_storage/test__archive.py's fake-runner tests instead. These
cover dry-run + validation only.
"""

import json
import os

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def sandbox_home(tmp_path):
    """Real temp HOME so archive manifest writes never touch ~/.scitex."""
    home = tmp_path / "cli-home"
    home.mkdir()
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev


def test_cli_archive_exits_zero(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert
    assert result.exit_code == 0


def test_cli_archive_requires_to_flag():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "/tmp"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_rejects_unknown_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "bogus"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_rejects_missing_source():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "/no/such/path", "--to", "nas"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_defaults_to_dry_run(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert
    assert f.exists()


def test_cli_archive_explicit_dry_run_flag_exits_zero(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--dry-run"])
    # Assert
    assert result.exit_code == 0


def test_cli_archive_yes_with_dry_run_does_not_mutate(tmp_path, sandbox_home):
    # Arrange -- --dry-run wins over --yes, so this never touches the network
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    runner.invoke(main, ["archive", str(source), "--to", "nas", "--yes", "--dry-run"])
    # Assert
    assert f.exists()


def test_cli_archive_json_reports_the_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--json"])
    # Assert
    assert json.loads(result.output)["destination"] == "nas"


def test_cli_archive_json_reports_applied_false_on_dry_run(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--json"])
    # Assert
    assert json.loads(result.output)["applied"] is False


def test_cli_restore_rejects_when_never_archived(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "never-archived"
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["restore", str(source)])
    # Assert
    assert result.exit_code != 0


# EOF
