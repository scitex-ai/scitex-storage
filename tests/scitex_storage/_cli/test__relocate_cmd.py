"""Unit tests for `scitex-storage relocate` / `relocate-restore` (CLI layer).

NO MOCKS (PA-306): real temp trees and a real sandboxed HOME. Pinned: the
default is a dry run, a conflict exits non-zero, and --yes then
relocate-restore round-trips.
"""

import json
import os

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
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "kk/a").mkdir(parents=True)
    (root / "kk/a/one.txt").write_text("1")
    plan = tmp_path / "plan.yaml"
    plan.write_text("moves:\n  - {from: kk, to: company}\n")
    return root, plan


def test_relocate_defaults_to_dry_run(repo, sandbox_home):
    # Arrange
    root, plan = repo
    # Act
    CliRunner().invoke(main, ["relocate", str(plan), "--root", str(root)])
    # Assert
    assert (root / "kk/a/one.txt").exists()


def test_relocate_yes_moves(repo, sandbox_home):
    # Arrange
    root, plan = repo
    # Act
    CliRunner().invoke(main, ["relocate", str(plan), "--root", str(root), "--yes"])
    # Assert
    assert (root / "company/a/one.txt").read_text() == "1"


def test_relocate_conflict_exits_nonzero(repo, sandbox_home):
    # Arrange
    root, plan = repo
    (root / "company/a").mkdir(parents=True)
    (root / "company/a/one.txt").write_text("DIFFERENT")
    # Act
    result = CliRunner().invoke(
        main, ["relocate", str(plan), "--root", str(root), "--yes"]
    )
    # Assert
    assert result.exit_code == 1


def test_relocate_restore_round_trips(repo, sandbox_home):
    # Arrange
    root, plan = repo
    out = CliRunner().invoke(
        main, ["relocate", str(plan), "--root", str(root), "--yes", "--json"]
    )
    run_id = json.loads(out.output)["run_id"]
    # Act
    CliRunner().invoke(main, ["relocate-restore", run_id])
    # Assert
    assert (root / "kk/a/one.txt").read_text() == "1"
