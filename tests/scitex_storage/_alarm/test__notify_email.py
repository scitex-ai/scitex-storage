"""Unit tests for the alarm email rail (scitex_storage._alarm._notify).

NO MOCKS (PA-306): every send is a real SMTP session (STARTTLS + AUTH) with a
local server on 127.0.0.1 (``smtp_server`` in conftest), configured through
the same ``SCITEX_NOTIFICATION_PROFILE_OPS_*`` variables an alarm host sets.
One assertion per test (STX-TQ007).
"""

import json
import os
import socket

from scitex_storage._alarm import (
    CRITICAL,
    OK,
    FilesystemAlarm,
    FleetAlarm,
    Rail,
    check_email_rail,
    email_notifier,
    notify_if_needed,
)
from scitex_storage._alarm._notify import email_event_id, resolve_email_profile


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


# --------------------------------------------------------------------------
# Email rail through a real SMTP session (local server, ``ops`` profile)
# --------------------------------------------------------------------------

ALARM = "STORAGE CRITICAL @ T\n  [critical] nas:/volume1 -- 1.0 GiB free"


def test_email_rail_reports_delivered_on_smtp_acceptance(ops_profile_env):
    # Arrange
    send = email_notifier()
    # Act
    delivered = send(ALARM)
    # Assert
    assert delivered is True


def test_default_profile_mails_dev_not_the_operator(ops_profile_env):
    """Operator policy 2026-11-27: system alarms go To dev@scitex.ai."""
    # Arrange
    send = email_notifier()
    # Act
    send(ALARM)
    # Assert
    assert ops_profile_env.messages[0]["envelope_to"] == "<dev@scitex.ai>"


def test_default_profile_sends_from_no_reply(ops_profile_env):
    # Arrange
    send = email_notifier()
    # Act
    send(ALARM)
    # Assert
    assert ops_profile_env.messages[0]["message"]["From"] == "no-reply@scitex.ai"


def test_email_rail_logs_in_with_the_password_file(ops_profile_env):
    # Arrange
    send = email_notifier()
    # Act
    send(ALARM)
    # Assert
    assert ops_profile_env.messages[0]["password_ok"] is True


def test_email_subject_names_the_host(ops_profile_env):
    # Arrange
    send = email_notifier()
    # Act
    send(ALARM)
    # Assert
    assert ops_profile_env.messages[0]["message"]["Subject"].startswith(
        f"[SciTeX] scitex-storage alarm on {socket.gethostname()}: STORAGE CRITICAL"
    )


def test_redelivery_of_the_same_alarm_is_not_mailed_twice(ops_profile_env):
    """A rail retried after a timeout must not duplicate an accepted mail."""
    # Arrange
    send = email_notifier()
    send(ALARM)
    # Act
    send(ALARM)
    # Assert
    assert len(ops_profile_env.messages) == 1


def test_email_profile_env_selects_another_profile(ops_profile_env):
    """$SCITEX_STORAGE_ALARM_EMAIL_PROFILE is real configuration."""
    # Arrange
    os.environ["SCITEX_STORAGE_ALARM_EMAIL_PROFILE"] = "nonexistent-profile"
    try:
        # Act
        name = resolve_email_profile()
    finally:
        os.environ.pop("SCITEX_STORAGE_ALARM_EMAIL_PROFILE", None)
    # Assert
    assert name == "nonexistent-profile"


def test_default_profile_is_ops():
    # Arrange
    os.environ.pop("SCITEX_STORAGE_ALARM_EMAIL_PROFILE", None)
    # Act
    name = resolve_email_profile()
    # Assert
    assert name == "ops"


def test_unconfigured_profile_fails_the_rail_by_name(tmp_path):
    """Never a silent reroute to another mail path."""
    # Arrange
    rails = [Rail("email", email_notifier("unconfigured-profile"))]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert "SCITEX_NOTIFICATION_PROFILE_UNCONFIGURED_PROFILE_FROM is not set" in (
        result.detail
    )


def test_unconfigured_profile_is_a_failed_push(tmp_path):
    # Arrange
    rails = [Rail("email", email_notifier("unconfigured-profile"))]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert result.is_failure is True


def test_group_readable_password_file_fails_the_rail(ops_profile_env):
    # Arrange
    pw = os.environ["SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD_FILE"]
    os.chmod(pw, 0o640)
    rails = [Rail("email", email_notifier())]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert "chmod 600" in result.detail


def test_smtp_refusal_fails_the_rail(refusing_smtp_server):
    # Arrange
    saved = dict(os.environ)
    os.environ.update(refusing_smtp_server.env)
    rails = [Rail("email", email_notifier())]
    try:
        # Act
        result = notify_if_needed(_critical(), rails, previous_level=OK)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    # Assert
    assert "SMTPRecipientsRefused" in result.detail


def test_check_email_rail_ok_for_a_configured_profile(ops_profile_env):
    # Arrange
    profile = "ops"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert result["ok"] is True


def test_check_email_rail_reports_the_recipient(ops_profile_env):
    # Arrange
    profile = "ops"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert (result["from"], result["to"]) == ("no-reply@scitex.ai", "dev@scitex.ai")


def test_check_email_rail_never_returns_the_password(ops_profile_env):
    # Arrange
    profile = "ops"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert ops_profile_env.password not in json.dumps(result)


def test_check_email_rail_sends_nothing(ops_profile_env):
    # Arrange
    profile = "ops"
    # Act
    check_email_rail(profile)
    # Assert
    assert ops_profile_env.messages == []


def test_check_email_rail_fails_for_an_unconfigured_profile():
    # Arrange
    profile = "unconfigured-profile"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert result["ok"] is False


def test_check_email_rail_fails_without_a_password(ops_profile_env):
    # Arrange
    os.environ.pop("SCITEX_NOTIFICATION_PROFILE_OPS_PASSWORD_FILE")
    # Act
    result = check_email_rail("ops")
    # Assert
    assert result["ok"] is False


def test_event_id_is_stable_for_the_same_alarm_text():
    # Arrange
    text = "STORAGE CRITICAL @ 2026-11-25T10:00:00Z"
    # Act
    ids = {email_event_id(text, "nas"), email_event_id(text, "nas")}
    # Assert
    assert len(ids) == 1




# --------------------------------------------------------------------------
# Password decrypted at send time from a gpg ciphertext (never a file)
# --------------------------------------------------------------------------


def test_encrypted_password_is_decrypted_for_the_login(ops_profile_gpg_env):
    # Arrange
    send = email_notifier()
    # Act
    send(ALARM)
    # Assert
    assert ops_profile_gpg_env.messages[0]["password_ok"] is True


def test_check_email_rail_decrypts_the_password(ops_profile_gpg_env):
    # Arrange
    profile = "ops"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert result["has_password"] is True


def test_check_email_rail_never_returns_the_decrypted_password(ops_profile_gpg_env):
    # Arrange
    profile = "ops"
    # Act
    result = check_email_rail(profile)
    # Assert
    assert ops_profile_gpg_env.password not in json.dumps(result)


def test_undecryptable_ciphertext_fails_the_rail_by_name(ops_profile_gpg_env, tmp_path):
    """A key that is absent (or needs a passphrase) is loud, not an empty login."""
    # Arrange
    os.environ["SCITEX_STORAGE_ALARM_EMAIL_GNUPGHOME"] = str(tmp_path / "no-keys")
    rails = [Rail("email", email_notifier())]
    # Act
    result = notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert "cannot decrypt" in result.detail


def test_undecryptable_ciphertext_sends_nothing(ops_profile_gpg_env, tmp_path):
    # Arrange
    os.environ["SCITEX_STORAGE_ALARM_EMAIL_GNUPGHOME"] = str(tmp_path / "no-keys")
    rails = [Rail("email", email_notifier())]
    # Act
    notify_if_needed(_critical(), rails, previous_level=OK)
    # Assert
    assert ops_profile_gpg_env.messages == []


# EOF
