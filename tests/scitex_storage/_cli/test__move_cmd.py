"""CLI tests for `scitex-storage move`.

NO MOCKS: real temp trees, real rsync, a real sandboxed HOME. Pinned: the
default is a dry run, --yes publishes a verified copy and stages the original,
and every refusal exits 19 with the source untouched.
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


@pytest.fixture
def sandbox_home(tmp_path):
    home = tmp_path / "home"
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


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / "old/pgdata"
    (source / "base/1").mkdir(parents=True)
    (source / "base/1/1259").write_bytes(b"page bytes")
    (source / "pg_notify").mkdir()
    source.chmod(0o700)
    (tmp_path / "new").mkdir()
    return source, tmp_path / "new/data"


def test_move_defaults_to_dry_run(tree, sandbox_home):
    # Arrange
    source, destination = tree
    # Act
    result = CliRunner().invoke(main, ["move", str(source), str(destination)])
    # Assert
    assert (result.exit_code, source.exists(), destination.exists()) == (
        0,
        True,
        False,
    )


def test_move_yes_publishes_verified_copy_and_stages_original(tree, sandbox_home):
    # Arrange
    source, destination = tree
    # Act
    result = CliRunner().invoke(
        main, ["move", str(source), str(destination), "--yes", "--json"]
    )
    out = json.loads(result.output)
    # Assert
    assert (
        result.exit_code,
        out["receipt"]["phase"],
        out["receipt"]["verification_method"],
        (destination / "base/1/1259").read_bytes(),
        (destination / "pg_notify").is_dir(),
        oct(destination.stat().st_mode & 0o777),
        source.exists(),
        (Path(out["receipt"]["staged_path"]) / "base/1/1259").read_bytes(),
    ) == (0, "retired", "content", b"page bytes", True, "0o700", False, b"page bytes")


def test_move_refuses_existing_destination_with_exit_19(tree, sandbox_home):
    # Arrange
    source, destination = tree
    destination.mkdir()
    # Act
    result = CliRunner().invoke(main, ["move", str(source), str(destination), "--yes"])
    # Assert
    assert (result.exit_code, "REFUSED" in result.output, source.exists()) == (
        19,
        True,
        True,
    )


def test_move_refuses_tree_over_entry_budget(tree, sandbox_home):
    # Arrange
    source, destination = tree
    # Act
    result = CliRunner().invoke(
        main, ["move", str(source), str(destination), "--yes", "--max-entries", "2"]
    )
    # Assert
    assert (result.exit_code, destination.exists(), source.exists()) == (
        19,
        False,
        True,
    )


def test_move_is_listed_in_help(sandbox_home):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["--help"])
    # Assert
    assert "move" in result.output
