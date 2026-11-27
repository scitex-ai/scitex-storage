"""CLI tests for ``scitex-storage tier``: dry-run never mutates, --yes is the gate.

A --yes run with units would shell out to a real NAS, so the applied path is
exercised here only where the plan is empty (no transport needed), and with
real local rsync in tests/integration/test_tier_roundtrip.py.
"""

import json
import os
import time

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main
from scitex_storage._cli._tier_cmd import SizeType

DAY = 86_400


@pytest.fixture
def sandbox_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


@pytest.fixture
def volume(tmp_path):
    root = tmp_path / "volume"
    root.mkdir()
    path = root / "old.dump"
    path.write_bytes(b"x" * 2_048)
    stamp = time.time() - 400 * DAY
    os.utime(path, (stamp, stamp))
    return root


def _invoke(*args):
    return CliRunner().invoke(main, ["tier", *args])


def test_dry_run_lists_the_cold_unit(volume, sandbox_home):
    # Arrange
    args = (str(volume), "--to", "scitex-nas-02", "--min-size", "1K", "--json")
    # Act
    result = _invoke(*args)
    # Assert
    payload = json.loads(result.output)
    assert [u["path"] for u in payload["plan"]["units"]] == [str(volume / "old.dump")]


def test_dry_run_leaves_the_file_in_place(volume, sandbox_home):
    # Arrange
    args = (str(volume), "--to", "scitex-nas-02", "--min-size", "1K")
    # Act
    _invoke(*args)
    # Assert
    assert (volume / "old.dump").exists()


def test_dry_run_writes_no_run_log(volume, sandbox_home):
    # Arrange
    args = (str(volume), "--to", "scitex-nas-02", "--min-size", "1K")
    # Act
    _invoke(*args)
    # Assert
    assert not (
        sandbox_home / ".scitex" / "scitex-storage" / "runtime" / "tier-logs"
    ).exists()


def test_yes_with_nothing_cold_writes_a_completed_run_log(volume, sandbox_home):
    # Arrange
    args = (str(volume), "--to", "scitex-nas-02", "--min-size", "1G", "--yes", "--json")
    # Act
    result = _invoke(*args)
    # Assert
    assert json.loads(result.output)["run"]["status"] == "completed"


def test_missing_root_is_a_usage_error(tmp_path, sandbox_home):
    # Arrange
    args = (str(tmp_path / "absent"), "--to", "scitex-nas-02")
    # Act
    result = _invoke(*args)
    # Assert
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "text, expected",
    [("512", 512), ("1K", 1_024), ("1.5G", 1_610_612_736), ("2TiB", 2 * 1_024**4)],
)
def test_size_type_parses_binary_units(text, expected):
    # Arrange
    size = SizeType()
    # Act
    value = size.convert(text, None, None)
    # Assert
    assert value == expected
