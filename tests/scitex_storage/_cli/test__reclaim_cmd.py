"""Unit tests for `scitex-storage reclaim` / `reclaim-restore`.

NO MOCKS (PA-306): reclaim is a local move, so the CLI is driven over real
temp dirs with a real sandboxed HOME. The properties pinned hardest are the
DEFAULT-DRY-RUN (a mutating verb must never move without --yes) and the
round-trip (reclaim then reclaim-restore leaves the tree where it started),
because those are what make a rough cleanup safe.
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


def _tree(root, name, n=2):
    d = root / name
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"f{i}.bin").write_bytes(b"\0")
    return d


def test_reclaim_dry_run_does_not_move_anything(tmp_path, sandbox_home):
    # Arrange -- the default invocation, no --yes.
    src = _tree(tmp_path, "target")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim", str(src)])
    # Assert -- source untouched, exit clean.
    assert result.exit_code == 0 and src.is_dir()


def test_reclaim_dry_run_says_would_move(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim", str(src)])
    # Assert
    assert "WOULD MOVE" in result.output


def test_reclaim_with_yes_moves_the_tree_aside(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim", str(src), "--yes"])
    # Assert
    assert result.exit_code == 0 and not src.exists() and (tmp_path / ".old").exists()


def test_reclaim_then_restore_round_trips(tmp_path, sandbox_home):
    # Arrange -- reclaim, capture the run id from --status, restore it.
    src = _tree(tmp_path, "target")
    runner = CliRunner()
    runner.invoke(main, ["reclaim", str(src), "--yes"])
    status = json.loads(
        runner.invoke(main, ["reclaim", "--status", "--json"]).output
    )
    run_id = status["manifests"][0]["run_id"]
    # Act
    result = runner.invoke(main, ["reclaim-restore", run_id])
    # Assert
    assert result.exit_code == 0 and src.is_dir()


def test_reclaim_no_paths_and_no_status_is_an_error(sandbox_home):
    # Arrange -- nothing to do is a usage error, not a silent no-op.
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim"])
    # Assert
    assert result.exit_code != 0


def test_reclaim_status_reports_restore_rate(tmp_path, sandbox_home):
    # Arrange -- two runs, one restored -> 50%.
    runner = CliRunner()
    runner.invoke(main, ["reclaim", str(_tree(tmp_path, "a")), "--yes"])
    runner.invoke(main, ["reclaim", str(_tree(tmp_path, "b")), "--yes"])
    status = json.loads(
        runner.invoke(main, ["reclaim", "--status", "--json"]).output
    )
    run_id = status["manifests"][-1]["run_id"]
    runner.invoke(main, ["reclaim-restore", run_id])
    # Act
    out = json.loads(runner.invoke(main, ["reclaim", "--status", "--json"]).output)
    # Assert
    assert out["restore_rate"] == 0.5


def test_reclaim_archive_root_moves_off_the_source_tree(tmp_path, sandbox_home):
    # Arrange -- the inode-relief shape: archive to a sibling location.
    src = _tree(tmp_path, "target")
    root = tmp_path / "elsewhere"
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["reclaim", str(src), "--archive-root", str(root), "--yes"]
    )
    # Assert
    assert result.exit_code == 0 and (root).exists() and not src.exists()


def test_reclaim_restore_unknown_run_is_a_clean_error(sandbox_home):
    # Arrange -- a bad run id must be a ClickException, not a traceback.
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim-restore", "nope-not-a-run"])
    # Assert
    assert result.exit_code != 0 and "Traceback" not in result.output


def test_reclaim_json_output_is_parseable(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim", str(src), "--json"])
    # Assert
    assert json.loads(result.output)["total_files"] == 2


# EOF
