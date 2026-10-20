"""Unit tests for the ``scitex-storage scrub`` CLI command.

The scrub ALWAYS contacts the destination host, so (like `archive --yes`)
the live path has no runner seam at the CLI layer and is covered in
``test__scrub.py`` instead. These cover validation only: a missing or
unreadable manifest must fail before any network is touched.
"""

from click.testing import CliRunner

from scitex_storage._cli import main


def test_cli_scrub_missing_manifest_fails(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scrub", str(tmp_path / "no-such.json")])
    # Assert
    assert result.exit_code != 0


def test_cli_scrub_malformed_manifest_fails(tmp_path):
    # Arrange
    runner = CliRunner()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    # Act
    result = runner.invoke(main, ["scrub", str(bad)])
    # Assert
    assert result.exit_code != 0


def test_cli_scrub_is_a_registered_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scrub", "--help"])
    # Assert
    assert result.exit_code == 0
