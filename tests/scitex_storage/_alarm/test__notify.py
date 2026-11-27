"""Unit tests for scitex_storage._alarm_notify (transition rule + transport).

NO MOCKS (PA-306) and none are needed: a notifier is just a callable, so
every transport case is driven with a real local function that records what
it was given. A function written in the test is data, not a fake -- there
is no patching, no library, and nothing that can drift from the real seam.

Each test makes exactly one assertion (STX-TQ007). Two directions are
pinned hardest, and they are opposites:

* FALSE REASSURANCE -- an unconfirmable delivery must never read as
  delivered, and a notifier that raises must be reported as a failure
  rather than swallowed. This is the direction that produced the original
  incident.
* FALSE ALARM -- an unchanged level must not push every cycle, because a
  reader who learns to swipe the alarm away is as uninformed as one who
  was never told, which is the same defect arriving a week later.
"""

import time

from scitex_storage._alarm import (
    CRITICAL,
    OK,
    UNKNOWN,
    UNKNOWN_STREAK_ALERT,
    WARN,
    FilesystemAlarm,
    FleetAlarm,
    Rail,
    format_blindness,
    notify_if_needed,
    should_notify,
)


def _recorder():
    """A real notifier that records its calls. Returns (fn, sent_list)."""
    sent: list[str] = []

    def fn(text: str) -> bool | None:
        sent.append(text)
        return True

    return fn, sent


# --------------------------------------------------------------------------
# Transition rule -- pure, and the reason the alarm does not become noise.
# --------------------------------------------------------------------------


def test_escalation_from_ok_to_warn_notifies():
    # Arrange
    previous, current = OK, WARN
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is True


def test_unchanged_critical_does_not_notify_every_cycle():
    """Still on fire is not new information; the payload still carries it."""
    # Arrange
    previous, current = CRITICAL, CRITICAL
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False


def test_recovery_notifies_exactly_once():
    """Without this the reader cannot tell a fixed problem from a forgotten one."""
    # Arrange
    previous, current = CRITICAL, OK
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is True


def test_recovery_from_ok_to_ok_stays_silent():
    # Arrange
    previous, current = OK, OK
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False


def test_no_previous_state_notifies_when_already_alarming():
    """A restarted process must not read its own memory loss as 'unchanged'."""
    # Arrange
    previous, current = None, CRITICAL
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is True


def test_no_previous_state_stays_silent_when_healthy():
    # Arrange
    previous, current = None, OK
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False


def test_de_escalation_between_alarming_levels_does_not_notify():
    """Slightly less on fire does not need a push; they were already told."""
    # Arrange
    previous, current = CRITICAL, WARN
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False


# --------------------------------------------------------------------------
# Transport -- dispatch is not delivery.
# --------------------------------------------------------------------------


def test_transition_sends_the_rendered_text():
    # Arrange
    fn, sent = _recorder()
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    notify_if_needed(alarm, fn, previous_level=OK)
    # Assert
    assert len(sent) == 1


def test_no_transition_does_not_call_the_notifier():
    # Arrange
    fn, sent = _recorder()
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    notify_if_needed(alarm, fn, previous_level=CRITICAL)
    # Assert
    assert sent == []


def test_not_attempted_is_distinguishable_from_failed():
    """"We chose not to speak" and "we spoke and it failed" must not merge."""
    # Arrange
    fn, _ = _recorder()
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, fn, previous_level=CRITICAL)
    # Assert
    assert result.is_failure is False


def test_unconfirmable_delivery_is_not_reported_as_delivered():
    """None means unknown; folding it into True is how a dead rail 'succeeds'."""
    # Arrange
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, lambda text: None, previous_level=OK)
    # Assert
    assert result.delivered is None


def test_unconfirmable_delivery_is_not_reported_as_failure_either():
    # Arrange
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, lambda text: None, previous_level=OK)
    # Assert
    assert result.is_failure is False


def test_refused_delivery_is_a_failure():
    # Arrange
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, lambda text: False, previous_level=OK)
    # Assert
    assert result.is_failure is True


# --------------------------------------------------------------------------
# A raising notifier must not take down the gather that feeds it.
# --------------------------------------------------------------------------


def _raiser(text: str) -> bool | None:
    raise RuntimeError("rail is down")


def test_raising_notifier_is_reported_as_failure_not_propagated():
    # Arrange
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, _raiser, previous_level=OK)
    # Assert
    assert result.is_failure is True


def test_raising_notifier_preserves_the_diagnosis():
    """Counted-but-unexplained failures are how a broken rail stays broken."""
    # Arrange
    alarm = FleetAlarm(level=CRITICAL, generated_at="T")
    # Act
    result = notify_if_needed(alarm, _raiser, previous_level=OK)
    # Assert
    assert "rail is down" in result.detail


def test_unknown_level_from_a_healthy_previous_state_does_not_push():
    """An unmeasurable fleet is reported, not paged on."""
    # Arrange
    alarm = FleetAlarm(level=UNKNOWN, generated_at="T")
    fn, sent = _recorder()
    # Act
    notify_if_needed(alarm, fn, previous_level=OK)
    # Assert
    assert sent == []


def test_sustained_blindness_notifies_at_the_streak_threshold():
    """ok -> unknown -> unknown -> ... would otherwise be silent FOREVER.

    A filesystem nobody can read is not healthy, it is UNMONITORED, and an
    unmonitored filesystem nobody is told about is this module's own defect
    in a narrower form. Found by scitex-db reviewing the rule.
    """
    # Arrange
    previous, current = UNKNOWN, UNKNOWN
    # Act
    result = should_notify(previous, current, unknown_streak=UNKNOWN_STREAK_ALERT)
    # Assert
    assert result is True


def test_sustained_blindness_announces_once_not_every_gather():
    """Past the threshold it goes quiet again -- same discipline as a critical."""
    # Arrange
    previous, current = UNKNOWN, UNKNOWN
    # Act
    result = should_notify(previous, current, unknown_streak=UNKNOWN_STREAK_ALERT + 1)
    # Assert
    assert result is False


def test_short_blindness_below_the_threshold_stays_silent():
    """A transient must still be absorbed; that is what the streak buys."""
    # Arrange
    previous, current = UNKNOWN, UNKNOWN
    # Act
    result = should_notify(previous, current, unknown_streak=UNKNOWN_STREAK_ALERT - 1)
    # Assert
    assert result is False


def test_blindness_message_does_not_claim_the_filesystem_is_healthy():
    """It must read as 'unread', never as a quieter capacity alarm."""
    # Arrange
    alarm = FleetAlarm(level=UNKNOWN, generated_at="T")
    # Act
    text = format_blindness(alarm, UNKNOWN_STREAK_ALERT)
    # Assert
    assert "not known to be healthy or unhealthy" in text


def test_losing_sight_of_an_alarming_filesystem_does_notify():
    """critical -> unknown is not recovery; silence would read as resolved."""
    # Arrange
    previous, current = CRITICAL, UNKNOWN
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is True


def test_unknown_after_unknown_stays_silent():
    """A persistently unreachable host must not page on every gather."""
    # Arrange
    previous, current = UNKNOWN, UNKNOWN
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False


def test_recovery_from_unknown_to_ok_stays_silent():
    """Nothing was ever reported, so there is nothing to declare resolved."""
    # Arrange
    previous, current = UNKNOWN, OK
    # Act
    result = should_notify(previous, current)
    # Assert
    assert result is False

# EOF

# --------------------------------------------------------------------------
# Unit tests for the rail chain and the email rail (#181).
# --------------------------------------------------------------------------

def _critical() -> FleetAlarm:
    return FleetAlarm(
        level=CRITICAL,
        generated_at="2026-11-25T10:00:00Z",
        filesystems=[
            FilesystemAlarm(
                host="nas", mount="/volume1", level=CRITICAL, reasons=["1.0 GiB free"]
            )
        ],
    )


def _db_down(text: str):
    raise ConnectionError("connection to server at 127.0.0.1:55432 refused")


def _true_recorder():
    sent: list[str] = []

    def fn(text: str):
        sent.append(text)
        return True

    return fn, sent


# --------------------------------------------------------------------------
# Chain semantics
# --------------------------------------------------------------------------


def test_fallback_delivers_when_the_primary_rail_raises():
    # Arrange
    email, _sent = _true_recorder()
    rails = [Rail("cards", _db_down), Rail("email", email)]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert result.rail == "email"


def test_fallback_message_actually_reaches_the_second_rail():
    # Arrange
    email, sent = _true_recorder()
    rails = [Rail("cards", _db_down), Rail("email", email)]
    # Act
    notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert "/volume1" in sent[0]


def test_fallback_is_loud_about_the_failed_primary():
    """Silent fallback would hide that the primary rail is broken."""
    # Arrange
    email, _sent = _true_recorder()
    rails = [Rail("cards", _db_down), Rail("email", email)]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert (
        result.detail.startswith("FALLBACK via email")
        and "cards: notifier raised ConnectionError" in result.detail
    )


def test_fallback_records_the_primary_error_structurally():
    # Arrange
    email, _sent = _true_recorder()
    rails = [Rail("cards", _db_down), Rail("email", email)]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert len(result.rail_errors) == 1


def test_primary_success_does_not_touch_the_fallback():
    # Arrange
    cards, _ = _true_recorder()
    email, sent = _true_recorder()
    rails = [Rail("cards", cards), Rail("email", email)]
    # Act
    notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert sent == []


def test_unconfirmed_primary_stops_the_chain():
    """cards returns None (stored, unread): it did not FAIL, so no email."""
    # Arrange
    email, sent = _true_recorder()
    rails = [Rail("cards", lambda text: None), Rail("email", email)]
    # Act
    notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert sent == []


def test_every_rail_failing_is_a_failure():
    # Arrange
    rails = [Rail("cards", _db_down), Rail("email", lambda text: False)]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert result.is_failure is True


def test_a_hung_rail_times_out_and_the_next_rail_delivers():
    """A dead DB can hang a connect; the alarm about it must not wait."""
    # Arrange
    email, _ = _true_recorder()
    rails = [Rail("cards", lambda text: time.sleep(1)), Rail("email", email)]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK, rail_timeout_s=0.2)
    # Assert
    assert result.rail == "email"


def test_single_callable_still_works_as_one_rail():
    # Arrange
    fn, _ = _true_recorder()
    # Act
    result = notify_if_needed(_critical(), fn, previous_level=OK)
    # Assert
    assert result.rail == "default"


# EOF
