"""Unit tests for the scitex-storage root CLI group (version/help/json flags).

Per-verb command tests live in their own files mirroring the split CLI
submodules: test__scan_cmd.py, test__duplicates_cmd.py, test__images_cmd.py,
test__sweep_cmd.py, test__archive_cmd.py.
"""

from click.testing import CliRunner

from scitex_storage._cli import main


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
