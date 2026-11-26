"""Tests for `scitex-storage purge` (CLI). NO MOCKS: real temp trees and HOME."""

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


def _cache(root):
    d = root / "cache"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    return d


def test_default_is_dry_run(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    CliRunner().invoke(main, ["purge", str(d)])
    # Assert
    assert d.exists()


def test_dry_run_writes_no_audit(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    CliRunner().invoke(main, ["purge", str(d)])
    # Assert
    assert not (sandbox_home / ".scitex").exists()


def test_yes_flag_deletes_cache_tree(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    CliRunner().invoke(main, ["purge", str(d), "--yes"])
    # Assert
    assert not d.exists()


def test_json_reports_audit_dir(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    out = CliRunner().invoke(main, ["purge", str(d), "--yes", "--json"]).output
    # Assert
    assert "purge-logs" in json.loads(out)["log_dir"]


def test_purge_without_paths_exits_nonzero(sandbox_home):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["purge"])
    # Assert
    assert result.exit_code != 0


def test_dry_run_flag_wins_over_yes(tmp_path, sandbox_home):
    # Arrange
    d = _cache(tmp_path)
    # Act
    CliRunner().invoke(main, ["purge", str(d), "--yes", "--dry-run"])
    # Assert
    assert d.exists()
