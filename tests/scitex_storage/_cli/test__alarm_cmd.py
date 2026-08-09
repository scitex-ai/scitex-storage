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

from scitex_storage._alarm import CRITICAL, OK, should_notify
from scitex_storage._cli._alarm_cmd import read_previous_level, write_level


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


def test_no_temp_file_is_left_behind(tmp_path):
    """Atomic write means rename, not a litter of .tmp files in runtime/."""
    # Arrange
    path = tmp_path / "alarm-state.json"
    # Act
    write_level(path, CRITICAL, "T")
    # Assert
    assert list(tmp_path.glob("*.tmp")) == []

# EOF
