"""Unit tests for `scitex-storage inodes`.

NO MOCKS (PA-306), and none are needed. The exit-code paths are reachable
with real filesystems by moving the THRESHOLD rather than faking the disk:
`--warn-at 0` makes any real filesystem critical, `--warn-at 100.1` makes
any real filesystem fine, and a genuinely missing path is genuinely
unreadable. The renderers are pure functions over `InodeUsage` values, so
they are tested by passing values -- constructing a dataclass is data, not
a mock.

The EXIT CODES are this command's real contract: it is built to run
unattended, and an unattended caller reads the exit code, not the table.
The distinction pinned hardest below is 2 (could-not-look) being separate
from 0 (fine) -- a monitoring cron that cannot tell "healthy" from "never
read it" reports healthy for filesystems it never looked at.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_storage._cli._inodes_cmd import _format_report, _usage_dict, inodes_cmd
from scitex_storage._measure._inodes import (
    COULD_NOT_LOOK,
    InodeUsage,
    probe,
    usage_from_counts,
)


@pytest.fixture
def runner():
    return CliRunner()


def _skip_unless_measured(path):
    """Skip a test that needs a real inode table (btrfs/ZFS have none)."""
    if probe(path).verdict != "measured":
        pytest.skip("this filesystem has no fixed inode table")


# --------------------------------------------------------------------------
# Exit codes -- the contract for unattended callers.
# --------------------------------------------------------------------------


def test_exits_zero_when_under_the_threshold(runner, tmp_path):
    # Arrange -- no real filesystem is over 100.1% used.
    # Act
    result = runner.invoke(inodes_cmd, [str(tmp_path), "--warn-at", "100.1"])
    # Assert
    assert result.exit_code == 0


def test_exits_one_when_at_or_over_the_threshold(runner, tmp_path):
    # Arrange -- every real filesystem is at or over 0% used.
    _skip_unless_measured(tmp_path)
    # Act
    result = runner.invoke(inodes_cmd, [str(tmp_path), "--warn-at", "0"])
    # Assert
    assert result.exit_code == 1


def test_exits_two_when_a_path_could_not_be_looked_at(runner, tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    result = runner.invoke(inodes_cmd, [str(missing)])
    # Assert -- NOT 0. A cron must never read this as healthy.
    assert result.exit_code == 2


def test_a_critical_path_outranks_an_unreadable_one(runner, tmp_path):
    # Arrange -- one real emergency plus one unreadable path. The emergency
    # is actionable now, so it must not be masked by the unknown.
    _skip_unless_measured(tmp_path)
    missing = tmp_path / "definitely-not-here"
    # Act
    result = runner.invoke(inodes_cmd, [str(tmp_path), str(missing), "--warn-at", "0"])
    # Assert
    assert result.exit_code == 1


def test_defaults_to_the_current_directory_when_given_no_path(runner):
    # Arrange -- the cwd always exists, so this must not be an unknown.
    # Act
    result = runner.invoke(inodes_cmd, ["--warn-at", "100.1"])
    # Assert
    assert result.exit_code == 0


# --------------------------------------------------------------------------
# JSON output.
# --------------------------------------------------------------------------


def test_json_output_is_parseable(runner, tmp_path):
    # Arrange
    # Act
    result = runner.invoke(inodes_cmd, [str(tmp_path), "--json", "--warn-at", "100.1"])
    # Assert
    assert json.loads(result.output)["results"][0]["path"] == str(tmp_path)


def test_json_output_carries_the_verdict(runner, tmp_path):
    # Arrange
    _skip_unless_measured(tmp_path)
    # Act
    result = runner.invoke(inodes_cmd, [str(tmp_path), "--json", "--warn-at", "100.1"])
    # Assert
    assert json.loads(result.output)["results"][0]["verdict"] == "measured"


def test_json_output_reports_null_not_zero_for_an_unreadable_path(runner, tmp_path):
    # Arrange -- the JSON contract must carry the same honesty as the API:
    # a consumer must not be able to read a 0 that was never measured.
    missing = tmp_path / "definitely-not-here"
    # Act
    result = runner.invoke(inodes_cmd, [str(missing), "--json"])
    # Assert
    assert json.loads(result.output)["results"][0]["percent_used"] is None


def test_json_output_reports_the_could_not_look_verdict_by_name(runner, tmp_path):
    # Arrange
    missing = tmp_path / "definitely-not-here"
    # Act
    result = runner.invoke(inodes_cmd, [str(missing), "--json"])
    # Assert
    assert json.loads(result.output)["results"][0]["verdict"] == "could-not-look"


def test_json_output_reports_every_path_given(runner, tmp_path):
    # Arrange
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    # Act
    result = runner.invoke(inodes_cmd, [str(a), str(b), "--json", "--warn-at", "100.1"])
    # Assert
    assert len(json.loads(result.output)["results"]) == 2


def test_usage_dict_rounds_percent_for_stable_output():
    # Arrange -- 1/3 used, an unterminating decimal.
    usage = usage_from_counts("/x", total=3, free=2)
    # Act
    row = _usage_dict(usage)
    # Assert
    assert row["percent_used"] == 33.33


# --------------------------------------------------------------------------
# Text rendering -- pure functions over InodeUsage values.
# --------------------------------------------------------------------------


def test_text_output_flags_a_critical_filesystem():
    # Arrange -- 99% used.
    usage = usage_from_counts("/x", total=1000, free=10)
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "CRITICAL" in report


def test_text_output_does_not_flag_a_healthy_filesystem():
    # Arrange -- 25% used.
    usage = usage_from_counts("/x", total=1000, free=750)
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "CRITICAL" not in report


def test_text_output_marks_an_unreadable_path_rather_than_omitting_it():
    # Arrange -- silence would read as "nothing wrong here".
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK, detail="boom")
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "COULD-NOT-LOOK" in report


def test_text_output_marks_a_dynamic_filesystem_as_not_applicable():
    # Arrange -- btrfs/ZFS.
    usage = usage_from_counts("/x", total=0, free=0)
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "NOT-APPLICABLE" in report


def test_text_output_shows_a_question_mark_not_a_zero_for_an_unmeasured_path():
    # Arrange -- the whole point: never render an unmeasured filesystem as
    # a reassuring 0%.
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK, detail="boom")
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "0.0" not in report


def test_text_output_explains_an_unmeasured_path():
    # Arrange
    usage = InodeUsage(path=Path("/x"), verdict=COULD_NOT_LOOK, detail="stale handle")
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "stale handle" in report


def test_text_output_names_the_mount_the_figures_came_from():
    # Arrange -- a figure attributed to the wrong thing is a dangerous
    # near-miss (a plain filesystem and a GPFS project fileset report very
    # different denominators through the same syscall), so which mount the
    # number came from is stated in-band rather than left to the docs.
    usage = usage_from_counts("/x", 1000, 750, mount="/data", fstype="gpfs")
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "/data" in report


def test_text_output_states_what_the_denominator_means():
    # Arrange
    usage = usage_from_counts("/x", 1000, 750, mount="/data", fstype="gpfs")
    # Act
    report = _format_report([usage], 90.0)
    # Assert
    assert "quota" in report.lower()


# EOF
