"""Unit tests for scitex_storage._alarm (snapshot -> pushed alarm).

NO MOCKS (PA-306), and none are needed: every function here is pure over
dataclasses, so each case is exercised by constructing plain values, which
is data rather than a fake.

Each test makes exactly one assertion (STX-TQ007). The cases pinned
hardest are the ones that fail in the direction of FALSE REASSURANCE,
because that is the direction that produced the incident this module
exists for -- a host reached 364 MB free on 393 GB and nothing reported
it. So: an empty gather must not read as healthy, an unmeasurable
filesystem must not read as healthy, and the inode threshold must fire on
its own, since a byte-only alarm is blind to the exhaustion that fails
writes while ``df`` still shows free space.

The complementary direction is pinned too -- UNKNOWN alone must NOT push,
because an alarm that fires on every transient unreachable host trains its
reader to ignore the channel, which is the same defect arriving later.
"""

from scitex_storage._alarm import (
    CRITICAL,
    CRITICAL_FREE_BYTES,
    OK,
    UNKNOWN,
    WARN,
    WARN_FREE_BYTES,
    FleetAlarm,
    evaluate_row,
    evaluate_snapshot,
    format_alarm,
)
from scitex_storage._fleet_status import FLAG_PERCENT, FleetSnapshot, HostStorage

GIB = 1024**3


def _row(**kw) -> HostStorage:
    """A healthy row by default; each test perturbs exactly one thing."""
    base = dict(
        host="h",
        role="tier1",
        mount="/m",
        avail_bytes=500 * GIB,
        used_pct=10.0,
        inode_used_pct=10.0,
    )
    base.update(kw)
    return HostStorage(**base)


# --------------------------------------------------------------------------
# Absolute floors -- "how long have I got", which a percentage cannot answer.
# --------------------------------------------------------------------------


def test_critical_floor_fires_even_when_percentage_looks_healthy():
    """A huge volume can sit under the floor at a low percentage used."""
    # Arrange
    row = _row(avail_bytes=CRITICAL_FREE_BYTES - 1, used_pct=1.0, inode_used_pct=1.0)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == CRITICAL


def test_warn_floor_fires_at_the_boundary():
    """At/under the floor, not merely below it -- boundaries are where bugs live."""
    # Arrange
    row = _row(avail_bytes=WARN_FREE_BYTES)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == WARN


def test_free_space_just_above_the_warn_floor_is_ok():
    # Arrange
    row = _row(avail_bytes=WARN_FREE_BYTES + 1)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == OK


# --------------------------------------------------------------------------
# Percentage -- catches a small volume nearly full, where the floor does not.
# --------------------------------------------------------------------------


def test_space_percentage_fires_even_with_generous_free_bytes():
    """A large volume over the percent threshold still has plenty free."""
    # Arrange
    row = _row(avail_bytes=500 * GIB, used_pct=FLAG_PERCENT)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == WARN


# --------------------------------------------------------------------------
# Inodes -- the blind spot a byte-only alarm has by construction.
# --------------------------------------------------------------------------


def test_inode_threshold_fires_with_space_entirely_healthy():
    # Arrange
    row = _row(inode_used_pct=FLAG_PERCENT)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == WARN


def test_inode_reason_names_the_silent_failure_mode():
    """The text must say WHY inodes matter; a bare percentage gets ignored."""
    # Arrange
    row = _row(inode_used_pct=FLAG_PERCENT)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert "df still shows free space" in " ".join(alarm.reasons)


def test_space_and_inode_reasons_are_both_recorded():
    """They fail independently; collapsing them hides one behind the other."""
    # Arrange
    row = _row(avail_bytes=1 * GIB, inode_used_pct=99.0)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert len(alarm.reasons) == 2


# --------------------------------------------------------------------------
# Unknown is a level, never a silence -- the false-reassurance direction.
# --------------------------------------------------------------------------


def test_row_with_nothing_measured_is_unknown_not_ok():
    # Arrange
    row = _row(avail_bytes=None, used_pct=None, inode_used_pct=None)
    # Act
    alarm = evaluate_row(row)
    # Assert
    assert alarm.level == UNKNOWN


def test_empty_snapshot_is_unknown_not_ok():
    """"Measured nothing" and "measured everything, all healthy" differ."""
    # Arrange
    snapshot = FleetSnapshot(rows=[])
    # Act
    alarm = evaluate_snapshot(snapshot)
    # Assert
    assert alarm.level == UNKNOWN


def test_unknown_alone_does_not_push():
    """One unreachable host is a transient, not a page."""
    # Arrange
    snapshot = FleetSnapshot(
        rows=[_row(avail_bytes=None, used_pct=None, inode_used_pct=None)]
    )
    # Act
    alarm = evaluate_snapshot(snapshot)
    # Assert
    assert alarm.should_push is False


def test_unknown_is_not_counted_as_healthy_in_the_message():
    # Arrange
    snapshot = FleetSnapshot(
        rows=[_row(avail_bytes=None, used_pct=None, inode_used_pct=None)]
    )
    # Act
    text = format_alarm(evaluate_snapshot(snapshot))
    # Assert
    assert "1 unmeasured" in text


# --------------------------------------------------------------------------
# Fleet roll-up.
# --------------------------------------------------------------------------


def test_fleet_level_is_the_worst_filesystem():
    # Arrange
    snapshot = FleetSnapshot(rows=[_row(), _row(host="h2", avail_bytes=1 * GIB)])
    # Act
    alarm = evaluate_snapshot(snapshot)
    # Assert
    assert alarm.level == CRITICAL


def test_alarming_filesystem_triggers_a_push():
    # Arrange
    snapshot = FleetSnapshot(rows=[_row(avail_bytes=1 * GIB)])
    # Act
    alarm = evaluate_snapshot(snapshot)
    # Assert
    assert alarm.should_push is True


def test_healthy_fleet_does_not_push():
    # Arrange
    snapshot = FleetSnapshot(rows=[_row()])
    # Act
    alarm = evaluate_snapshot(snapshot)
    # Assert
    assert alarm.should_push is False


def test_default_fleet_alarm_is_ok_shaped_not_empty():
    """The dataclass returns the same shape even with nothing in it."""
    # Arrange
    alarm = FleetAlarm()
    # Act
    level = alarm.level
    # Assert
    assert level == OK


# --------------------------------------------------------------------------
# Message rendering -- read under pressure, so it must name host and mount.
# --------------------------------------------------------------------------


def test_message_names_the_host_and_mount():
    # Arrange
    snapshot = FleetSnapshot(
        rows=[_row(host="compute-04", mount="/", avail_bytes=1 * GIB)]
    )
    # Act
    text = format_alarm(evaluate_snapshot(snapshot))
    # Assert
    assert "compute-04:/" in text


def test_critical_sorts_above_warn_in_the_message():
    # Arrange
    snapshot = FleetSnapshot(
        rows=[
            _row(host="w", avail_bytes=WARN_FREE_BYTES),
            _row(host="c", avail_bytes=1 * GIB),
        ]
    )
    # Act
    text = format_alarm(evaluate_snapshot(snapshot))
    # Assert
    assert text.index("[critical]") < text.index("[warn]")

# EOF
