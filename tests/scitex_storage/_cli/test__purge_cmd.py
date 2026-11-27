"""Unit tests for the ``scitex-storage purge`` CLI command.

The CLI inspects the real process table, so on a non-root runner its plan
may refuse with "could not inspect"; that refusal is the engine working.
These tests therefore cover registration and refusals that hold for every
user. The deleting path is covered in ``_transfer/test__purge.py``.
"""

import json
import os

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


@pytest.fixture(autouse=True)
def home(tmp_path):
    """Keep audit records written by --yes inside the test's own tree."""
    path = tmp_path / "home"
    path.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = previous


def _tree(tmp_path):
    root = tmp_path / "work" / "stale"
    root.mkdir(parents=True)
    (root / "a.bin").write_bytes(b"alpha")
    return root


def test_cli_purge_is_a_registered_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["purge", "--help"])
    # Assert
    assert result.exit_code == 0


def test_cli_purge_requires_a_reason(tmp_path):
    # Arrange
    root = _tree(tmp_path)
    # Act
    result = CliRunner().invoke(main, ["purge", str(root), "--confirm", str(root)])
    # Assert
    assert result.exit_code == 2


def test_cli_purge_mismatched_confirm_exits_refused(tmp_path):
    # Arrange
    root = _tree(tmp_path)
    args = ["purge", str(root), "--confirm", str(root.parent), "--reason", "x"]
    # Act
    result = CliRunner().invoke(main, [*args, "--yes"])
    # Assert
    assert result.exit_code == 18


def test_cli_purge_refusal_deletes_nothing(tmp_path):
    # Arrange
    root = _tree(tmp_path)
    args = ["purge", str(root), "--confirm", str(root.parent), "--reason", "x"]
    # Act
    CliRunner().invoke(main, [*args, "--yes"])
    # Assert
    assert (root / "a.bin").exists()


def test_cli_purge_json_names_the_refusal(tmp_path):
    # Arrange
    root = _tree(tmp_path)
    args = ["purge", str(root), "--confirm", "elsewhere", "--reason", "x", "--json"]
    # Act
    result = CliRunner().invoke(main, args)
    # Assert
    assert "--confirm" in json.loads(result.output)["refused"]


def test_cli_purge_yes_audits_the_refusal(tmp_path, home):
    # Arrange
    root = _tree(tmp_path)
    args = ["purge", str(root), "--confirm", str(root.parent), "--reason", "x"]
    # Act
    CliRunner().invoke(main, [*args, "--yes"])
    # Assert
    logs = home / ".scitex" / "scitex-storage" / "runtime" / "purge-logs"
    assert [json.loads(p.read_text())["status"] for p in logs.glob("*.json")] == [
        "refused"
    ]


def test_cli_purge_dry_run_wins_over_yes(tmp_path):
    # Arrange
    root = _tree(tmp_path)
    args = ["purge", str(root), "--confirm", str(root), "--reason", "x"]
    # Act
    CliRunner().invoke(main, [*args, "--yes", "--dry-run"])
    # Assert
    assert (root / "a.bin").exists()


# EOF
