"""Unit tests for scitex_storage._alarm._repeat (per-filesystem repeat rule).

Pure function, real data, no mocks: memory is a dict and the clock is a
number passed in. One assertion per test (STX-TQ007).
"""

from scitex_storage._alarm import (
    CRITICAL,
    OK,
    UNKNOWN,
    WARN,
    FilesystemAlarm,
    FleetAlarm,
    decide_filesystems,
)

H = 3600.0
T0 = 1_800_000_000.0
KEY = "nas:/volume1"


def _fleet(*levels_by_mount: tuple[str, str]) -> FleetAlarm:
    fss = [
        FilesystemAlarm(host="nas", mount=m, level=lv, reasons=[f"{lv} reason"])
        for m, lv in levels_by_mount
    ]
    return FleetAlarm(level=OK, generated_at="T", filesystems=fss)


def _one(level: str) -> FleetAlarm:
    return _fleet(("/volume1", level))


def _told(level, **sent_at):
    return {KEY: {"announced": level, "sent_at": dict(sent_at)}}


def test_first_alarm_without_memory_mails():
    # Arrange
    alarm = _one(WARN)
    # Act
    decision = decide_filesystems(alarm, {}, T0)
    # Assert
    assert decision.send is True


def test_first_run_on_a_healthy_filesystem_stays_silent():
    # Arrange
    alarm = _one(OK)
    # Act
    decision = decide_filesystems(alarm, {}, T0)
    # Assert
    assert decision.send is False


def test_ok_to_ok_never_mails():
    # Arrange
    memory = _told(OK, ok=T0 - 100 * H)
    # Act
    decision = decide_filesystems(_one(OK), memory, T0)
    # Assert
    assert decision.send is False


def test_unknown_to_ok_never_mails():
    # Arrange
    memory = _told(UNKNOWN)
    # Act
    decision = decide_filesystems(_one(OK), memory, T0)
    # Assert
    assert decision.send is False


def test_recovery_after_warn_mails():
    # Arrange
    memory = _told(WARN, warn=T0 - H)
    # Act
    decision = decide_filesystems(_one(OK), memory, T0)
    # Assert
    assert decision.changes == [f"{KEY} recovered: warn -> ok"]


def test_recovery_is_not_held_back_by_a_recent_recovery():
    """Suppressing a recovery would leave the reader thinking it is still full."""
    # Arrange
    memory = _told(CRITICAL, critical=T0 - 60, ok=T0 - 120)
    # Act
    decision = decide_filesystems(_one(OK), memory, T0)
    # Assert
    assert decision.send is True


def test_unchanged_warn_does_not_mail():
    # Arrange
    memory = _told(WARN, warn=T0 - 100 * H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0)
    # Assert
    assert decision.send is False


def test_flapping_warn_within_the_window_is_suppressed():
    """warn (mailed) -> ok (recovery mailed) -> warn again an hour later."""
    # Arrange
    memory = _told(OK, warn=T0 - H, ok=T0 - 0.5 * H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.send is False


def test_suppression_is_reported():
    # Arrange
    memory = _told(OK, warn=T0 - H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.reason.startswith("repeat suppressed:")


def test_suppressed_alarm_is_not_recorded_as_announced():
    # Arrange
    memory = _told(OK, warn=T0 - H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.memory[KEY]["announced"] == OK


def test_suppressed_alarm_is_mailed_once_the_window_has_passed():
    # Arrange
    memory = _told(OK, warn=T0 - 7 * H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.send is True


def test_recovery_after_a_suppressed_alarm_stays_silent():
    """The reader was never told about the suppressed WARN, so no 'resolved'."""
    # Arrange
    held = decide_filesystems(_one(WARN), _told(OK, warn=T0 - H), T0)
    # Act
    decision = decide_filesystems(_one(OK), held.memory, T0 + 600)
    # Assert
    assert decision.send is False


def test_escalation_warn_to_critical_mails_immediately():
    """A WARN mailed a minute ago must not delay CRITICAL."""
    # Arrange
    memory = _told(WARN, warn=T0 - 60)
    # Act
    decision = decide_filesystems(_one(CRITICAL), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.changes == [f"{KEY} escalated: warn -> critical"]


def test_repeated_critical_within_the_window_is_suppressed():
    """critical -> warn -> critical flapping at the critical floor."""
    # Arrange
    memory = _told(WARN, warn=T0 - 2 * H, critical=T0 - H)
    # Act
    decision = decide_filesystems(_one(CRITICAL), memory, T0, repeat_after_s=6 * H)
    # Assert
    assert decision.send is False


def test_de_escalation_does_not_mail():
    # Arrange
    memory = _told(CRITICAL, critical=T0 - H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0)
    # Assert
    assert decision.send is False


def test_de_escalation_lowers_the_announced_level():
    # Arrange
    memory = _told(CRITICAL, critical=T0 - H)
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0)
    # Assert
    assert decision.memory[KEY]["announced"] == WARN


def test_losing_sight_of_an_alarming_filesystem_mails():
    # Arrange
    memory = _told(CRITICAL, critical=T0 - H)
    # Act
    decision = decide_filesystems(_one(UNKNOWN), memory, T0)
    # Assert
    assert decision.send is True


def test_losing_sight_of_a_healthy_filesystem_stays_silent():
    # Arrange
    memory = _told(OK)
    # Act
    decision = decide_filesystems(_one(UNKNOWN), memory, T0)
    # Assert
    assert decision.send is False


def test_second_filesystem_going_warn_mails_while_the_first_stays_warn():
    """Invisible to the fleet-level rule: the fleet level did not change."""
    # Arrange
    memory = _told(WARN, warn=T0 - H)
    alarm = _fleet(("/volume1", WARN), ("/volume2", WARN))
    # Act
    decision = decide_filesystems(alarm, memory, T0)
    # Assert
    assert decision.changes == ["nas:/volume2 is warn"]


def test_window_is_per_filesystem():
    # Arrange
    memory = {
        KEY: {"announced": OK, "sent_at": {"warn": T0 - H}},
        "nas:/volume2": {"announced": OK, "sent_at": {}},
    }
    alarm = _fleet(("/volume1", WARN), ("/volume2", WARN))
    # Act
    decision = decide_filesystems(alarm, memory, T0)
    # Assert
    assert decision.changes == ["nas:/volume2 is warn (was ok)"]


def test_memory_of_filesystems_absent_from_this_gather_is_kept():
    # Arrange
    memory = {"other:/x": {"announced": WARN, "sent_at": {"warn": T0 - H}}}
    # Act
    decision = decide_filesystems(_one(OK), memory, T0)
    # Assert
    assert "other:/x" in decision.memory


def test_corrupt_memory_entry_is_forgotten_and_re_announces():
    # Arrange
    memory = {KEY: "garbage"}
    # Act
    decision = decide_filesystems(_one(WARN), memory, T0)
    # Assert
    assert decision.send is True


def test_input_memory_is_not_mutated():
    # Arrange
    memory = _told(OK)
    # Act
    decide_filesystems(_one(WARN), memory, T0)
    # Assert
    assert memory == _told(OK)


# EOF
