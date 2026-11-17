"""Unit tests for the ``scitex-storage skills`` command group.

The package ships battery-included agent skills
(``src/scitex_storage/_skills/scitex-storage/``); the CLI group is the
required list/get/install surface over them (audit §1a). These cover the
read/install paths only -- installing for real writes outside the repo,
so the apply path is previewed with --dry-run.
"""

from click.testing import CliRunner

from scitex_storage._cli import main


def test_cli_skills_list_names_bundled_leaves():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "list"])
    # Assert
    assert "05_safety-contract" in result.output


def test_cli_skills_list_exits_zero():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "list"])
    # Assert
    assert result.exit_code == 0


def test_cli_skills_get_prints_leaf_content():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "get", "05_safety-contract"])
    # Assert
    assert "Safety contract" in result.output


def test_cli_skills_get_unknown_name_fails():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["skills", "get", "no-such-leaf"])
    # Assert
    assert result.exit_code != 0


def test_cli_skills_install_dry_run_writes_nothing(tmp_path):
    # Arrange
    runner = CliRunner()
    dest = tmp_path / "skills-dest"
    # Act
    _result = runner.invoke(
        main, ["skills", "install", "--dry-run", "--dest", str(dest)]
    )
    # Assert
    assert not dest.exists()


def test_cli_skills_group_is_eagerly_discoverable():
    """The audit (§1a) reads the attached-command mapping, not the lazy registry."""
    # Arrange
    import click

    from scitex_storage._cli import main
    # Act
    group = main.commands.get("skills")
    # Assert
    assert isinstance(group, click.Group)
