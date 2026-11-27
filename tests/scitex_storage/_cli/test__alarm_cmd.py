"""Unit tests for scitex_storage._cli._alarm_cmd (the state the rule needs).

NO MOCKS (PA-306). The state helpers do real I/O, so they are tested
against a REAL filesystem via pytest's ``tmp_path`` -- a real directory is
not a fake, and patching ``open`` would test the patch rather than the
behaviour.

Each test makes exactly one assertion (STX-TQ007). The case pinned hardest
is amnesia: every way of failing to read the state file must yield
``None`` ("we do not know what we last said"), because the alternative --
returning the current level, or treating a missing file as "unchanged" --
silences a fresh process standing in front of a full disk. That is a gate
that cannot fail, and it is the exact shape of the incident this verb
exists for.
"""

import json

import click
import pytest
from click.testing import CliRunner

from scitex_storage._alarm import CRITICAL, OK, UNKNOWN, should_notify
from scitex_storage._cli._alarm_cmd import (
    EXIT_PUSH_REFUSED,
    _default_state_path,
    alarm_cmd,
    next_unknown_streak,
    parse_rails,
    read_previous_level,
    read_unknown_streak,
    write_level,
)
from scitex_storage._fleet_status import gather_fleet_snapshot


def test_written_level_is_read_back_unchanged(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    # Act
    write_level(path, CRITICAL, "2026-08-09T16:00:00Z")
    # Assert
    assert read_previous_level(path) == CRITICAL


def test_missing_state_file_reads_as_unknown_not_as_ok(tmp_path):
    """Absent state must not imply a healthy previous reading."""
    # Arrange
    path = tmp_path / "never-written.json"
    # Act
    previous = read_previous_level(path)
    # Assert
    assert previous is None


def test_malformed_state_file_reads_as_unknown(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    path.write_text("{not json at all", encoding="utf-8")
    # Act
    previous = read_previous_level(path)
    # Assert
    assert previous is None


def test_state_file_without_the_level_key_reads_as_unknown(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    path.write_text(json.dumps({"generated_at": "T"}), encoding="utf-8")
    # Act
    previous = read_previous_level(path)
    # Assert
    assert previous is None


def test_non_string_level_reads_as_unknown(tmp_path):
    """A number where a level belongs is corruption, not a level."""
    # Arrange
    path = tmp_path / "alarm-state.json"
    path.write_text(json.dumps({"level": 3}), encoding="utf-8")
    # Act
    previous = read_previous_level(path)
    # Assert
    assert previous is None


def test_lost_state_still_announces_an_existing_alarm(tmp_path):
    """The whole point of returning None: amnesia must not read as calm."""
    # Arrange
    path = tmp_path / "never-written.json"
    # Act
    notify = should_notify(read_previous_level(path), CRITICAL)
    # Assert
    assert notify is True


def test_lost_state_does_not_announce_a_healthy_fleet(tmp_path):
    """The complementary direction: amnesia is not an excuse to page."""
    # Arrange
    path = tmp_path / "never-written.json"
    # Act
    notify = should_notify(read_previous_level(path), OK)
    # Assert
    assert notify is False


def test_write_replaces_a_previous_level(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    write_level(path, CRITICAL, "T1")
    # Act
    write_level(path, OK, "T2")
    # Assert
    assert read_previous_level(path) == OK


def test_write_creates_the_runtime_directory(tmp_path):
    """A first run on a fresh host must not fail for want of a parent dir."""
    # Arrange
    path = tmp_path / "deep" / "nested" / "alarm-state.json"
    # Act
    write_level(path, OK, "T")
    # Assert
    assert path.exists()


def test_unknown_streak_advances_while_blind():
    # Arrange
    previous_streak = 2
    # Act
    streak = next_unknown_streak(previous_streak, UNKNOWN)
    # Assert
    assert streak == 3


def test_unknown_streak_resets_the_moment_anything_is_measured():
    """A streak surviving a good gather would page about a readable filesystem."""
    # Arrange
    previous_streak = 9
    # Act
    streak = next_unknown_streak(previous_streak, OK)
    # Assert
    assert streak == 0


def test_streak_survives_a_write_and_read_round_trip(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    # Act
    write_level(path, UNKNOWN, "T", 4)
    # Assert
    assert read_unknown_streak(path) == 4


def test_missing_streak_reads_as_zero_not_as_a_long_blindness(tmp_path):
    """A lost counter restarts the count rather than inventing history."""
    # Arrange
    path = tmp_path / "never-written.json"
    # Act
    streak = read_unknown_streak(path)
    # Assert
    assert streak == 0


def test_negative_streak_is_rejected_as_corruption(tmp_path):
    # Arrange
    path = tmp_path / "alarm-state.json"
    path.write_text(json.dumps({"unknown_streak": -5}), encoding="utf-8")
    # Act
    streak = read_unknown_streak(path)
    # Assert
    assert streak == 0


def test_no_temp_file_is_left_behind(tmp_path):
    """Atomic write means rename, not a litter of .tmp files in runtime/."""
    # Arrange
    path = tmp_path / "alarm-state.json"
    # Act
    write_level(path, CRITICAL, "T")
    # Assert
    assert list(tmp_path.glob("*.tmp")) == []

# EOF

# --------------------------------------------------------------------------
# CLI tests for ``alarm --path / --warn-free-gib / --crit-free-gib / --notify`` (#181).
# --------------------------------------------------------------------------

HUGE = "1000000000"  # GiB; any real filesystem is under this floor


@pytest.fixture
def runner():
    return CliRunner()


def _run(runner, tmp_path, *args, env=None):
    return runner.invoke(
        alarm_cmd,
        ["--path", str(tmp_path), "--state", str(tmp_path / "state.json"), *args],
        env={"HOME": str(tmp_path), **(env or {})},
    )


def test_local_gather_now_measures_free_bytes(tmp_path):
    """Before #181 avail_bytes stayed None, so the floors never fired."""
    # Arrange
    paths = [str(tmp_path)]
    # Act
    row = gather_fleet_snapshot(paths).rows[0]
    # Assert
    assert row.avail_bytes is not None and row.avail_bytes >= 0


def test_path_option_scopes_the_gather(runner, tmp_path):
    # Arrange
    args = ("--dry-run", "--json")
    # Act
    payload = json.loads(_run(runner, tmp_path, *args).output)
    # Assert
    assert [f["mount"] for f in payload["filesystems"]] == [str(tmp_path)]


def test_warn_floor_option_raises_warn(runner, tmp_path):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", "0", "--dry-run", "--json")
    # Act
    payload = json.loads(_run(runner, tmp_path, *args).output)
    # Assert
    assert payload["level"] == "warn"


def test_critical_floor_option_raises_critical(runner, tmp_path):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", HUGE, "--dry-run", "--json")
    # Act
    payload = json.loads(_run(runner, tmp_path, *args).output)
    # Assert
    assert payload["level"] == "critical"


def test_issue_spelling_critical_free_gib_is_accepted(runner, tmp_path):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--critical-free-gib", HUGE, "--dry-run", "--json")
    # Act
    payload = json.loads(_run(runner, tmp_path, *args).output)
    # Assert
    assert payload["level"] == "critical"


def test_inverted_floors_are_a_usage_error(runner, tmp_path):
    # Arrange
    args = ("--warn-free-gib", "10", "--crit-free-gib", "50", "--dry-run")
    # Act
    result = _run(runner, tmp_path, *args)
    # Assert
    assert result.exit_code == 2


def test_dry_run_records_no_state(runner, tmp_path):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", "0", "--dry-run")
    # Act
    _run(runner, tmp_path, *args)
    # Assert
    assert not (tmp_path / "state.json").exists()


def test_email_rail_delivers_a_real_alarm(runner, tmp_path, smtp_server):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", "0", "--notify", "email")
    # Act
    payload = json.loads(
        _run(runner, tmp_path, *args, "--json", env=smtp_server.env).output
    )
    # Assert
    assert (payload["rail"], payload["delivered"]) == ("email", True)


def test_email_rail_mails_dev_by_default(runner, tmp_path, smtp_server):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", "0", "--notify", "email")
    # Act
    _run(runner, tmp_path, *args, env=smtp_server.env)
    # Assert
    assert smtp_server.messages[0]["envelope_to"] == "<dev@scitex.ai>"


def test_refused_email_exits_push_refused(runner, tmp_path, refusing_smtp_server):
    # Arrange
    args = ("--warn-free-gib", HUGE, "--crit-free-gib", "0", "--notify", "email")
    # Act
    result = _run(runner, tmp_path, *args, env=refusing_smtp_server.env)
    # Assert
    assert result.exit_code == EXIT_PUSH_REFUSED


def test_unconfigured_email_profile_exits_push_refused(runner, tmp_path):
    """No silent reroute: an unconfigured profile is a refused push."""
    # Arrange
    args = (
        "--warn-free-gib",
        HUGE,
        "--crit-free-gib",
        "0",
        "--notify",
        "email",
        "--email-profile",
        "unconfigured-profile",
    )
    # Act
    result = _run(runner, tmp_path, *args)
    # Assert
    assert result.exit_code == EXIT_PUSH_REFUSED


def test_check_notify_passes_with_a_configured_profile(runner, tmp_path, smtp_server):
    # Arrange
    args = ("--notify", "email", "--check-notify")
    # Act
    result = _run(runner, tmp_path, *args, env=smtp_server.env)
    # Assert
    assert result.exit_code == 0


def test_check_notify_fails_with_an_unconfigured_profile(runner, tmp_path):
    # Arrange
    args = (
        "--notify",
        "email",
        "--email-profile",
        "unconfigured-profile",
        "--check-notify",
    )
    # Act
    result = _run(runner, tmp_path, *args)
    # Assert
    assert result.exit_code == EXIT_PUSH_REFUSED


def test_check_notify_sends_nothing_and_records_no_state(runner, tmp_path, smtp_server):
    # Arrange
    args = ("--notify", "email", "--check-notify")
    # Act
    _run(runner, tmp_path, *args, env=smtp_server.env)
    # Assert
    assert smtp_server.messages == [] and not (tmp_path / "state.json").exists()


def test_unknown_rail_is_refused():
    # Arrange
    spec = "cards,pager"

    # Act
    def parse():
        return parse_rails(spec)

    # Assert
    with pytest.raises(click.BadParameter):
        parse()


def test_rail_order_is_preserved():
    # Arrange
    spec = "email, cards"
    # Act
    names = parse_rails(spec)
    # Assert
    assert names == ["email", "cards"]


def test_each_path_set_gets_its_own_state_file():
    """A /volume1 alarm and the default alarm must not overwrite each other."""
    # Arrange
    scoped = ("/volume1",)
    # Act
    pair = (_default_state_path(scoped), _default_state_path(None))
    # Assert
    assert pair[0] != pair[1]


def test_state_file_is_independent_of_path_order():
    # Arrange
    a, b = ("/x", "/y"), ("/y", "/x")
    # Act
    pair = (_default_state_path(a), _default_state_path(b))
    # Assert
    assert pair[0] == pair[1]
