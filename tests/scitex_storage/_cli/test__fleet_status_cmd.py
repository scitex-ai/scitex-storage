"""Unit tests for `scitex-storage fleet-status`.

NO MOCKS (PA-306). The command's I/O is a file write and a real
statvfs-backed gather, both exercisable for real: `--demo` renders the
seeded snapshot (no network), `--output` writes to a real tmp file, and
the default output path is sandboxed by pointing `$HOME` at tmp_path --
the same pattern the archive/reclaim command tests use.
"""

import json

import pytest
from click.testing import CliRunner

from scitex_storage._cli._fleet_status_cmd import (
    _default_output,
    _snapshot_dict,
    fleet_status_cmd,
)
from scitex_storage._fleet_status import demo_snapshot


@pytest.fixture
def runner():
    return CliRunner()


# --------------------------------------------------------------------------
# HTML output.
# --------------------------------------------------------------------------


def test_demo_writes_a_dashboard_to_an_explicit_output(runner, tmp_path):
    # Arrange
    out = tmp_path / "board.html"
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo", "--output", str(out)])
    # Assert
    assert result.exit_code == 0
    assert out.exists()


def test_written_dashboard_is_self_contained_dark_mode_html(runner, tmp_path):
    # Arrange
    out = tmp_path / "board.html"
    # Act
    runner.invoke(fleet_status_cmd, ["--demo", "--output", str(out)])
    text = out.read_text(encoding="utf-8")
    # Assert
    assert text.startswith("<!doctype html>")
    assert "--bg: #12151a" in text


def test_default_output_lands_in_the_runtime_tree(runner, tmp_path, monkeypatch):
    # Arrange -- sandbox $HOME so the default path resolves under tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo"])
    # Assert
    expected = tmp_path / ".scitex/scitex-storage/runtime/fleet-status.html"
    assert result.exit_code == 0 and expected.exists()


def test_reports_the_flagged_and_could_not_look_counts(runner, tmp_path):
    # Arrange
    out = tmp_path / "board.html"
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo", "--output", str(out)])
    # Assert -- the seeded snapshot has 2 flagged + 2 could-not-look.
    assert "2 flagged" in result.output
    assert "2 could-not-look" in result.output


# --------------------------------------------------------------------------
# JSON output.
# --------------------------------------------------------------------------


def test_json_output_is_parseable(runner):
    # Arrange
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo", "--json"])
    # Assert
    assert json.loads(result.output)["total_filesystems"] == 7


def test_json_output_carries_the_three_state_verdict(runner):
    # Arrange
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo", "--json"])
    verdicts = {r["verdict"] for r in json.loads(result.output)["rows"]}
    # Assert
    assert "could-not-look" in verdicts


def test_json_output_reports_null_not_zero_for_an_unread_inode(runner):
    # Arrange -- a could-not-look row must carry null, never a 0 a consumer
    # could read as healthy.
    # Act
    result = runner.invoke(fleet_status_cmd, ["--demo", "--json"])
    rows = json.loads(result.output)["rows"]
    nas = next(r for r in rows if r["host"] == "nas")
    # Assert
    assert nas["inode_used_pct"] is None


def test_json_output_does_not_write_a_file(runner, tmp_path, monkeypatch):
    # Arrange -- --json is stdout-only; it must not also touch the runtime tree.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Act
    runner.invoke(fleet_status_cmd, ["--demo", "--json"])
    # Assert
    assert not (tmp_path / ".scitex/scitex-storage/runtime/fleet-status.html").exists()


# --------------------------------------------------------------------------
# Local (non-demo) gather -- real statvfs, no network.
# --------------------------------------------------------------------------


def test_local_gather_writes_a_dashboard(runner, tmp_path):
    # Arrange -- no --demo: measure THIS host's real filesystems.
    out = tmp_path / "local.html"
    # Act
    result = runner.invoke(fleet_status_cmd, ["--output", str(out)])
    # Assert
    assert result.exit_code == 0 and out.exists()


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def test_default_output_is_under_the_runtime_dir():
    # Arrange / Act
    path = _default_output()
    # Assert
    assert path.name == "fleet-status.html"
    assert "scitex-storage/runtime" in str(path)


def test_snapshot_dict_round_trips_through_json():
    # Arrange
    payload = _snapshot_dict(demo_snapshot())
    # Act -- must be JSON-serialisable with no custom encoder.
    restored = json.loads(json.dumps(payload))
    # Assert
    assert restored["total_hosts"] == payload["total_hosts"]


# EOF
