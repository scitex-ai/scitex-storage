"""CLI tests for per-filesystem repeat suppression (state round trip).

Real gathers of ``tmp_path``, a real state file, a real delivering rail.
One assertion per test (STX-TQ007).
"""

import json
import stat

import pytest
from click.testing import CliRunner

from scitex_storage._cli._alarm_cmd import EXIT_PUSH_REFUSED, alarm_cmd, read_memory

HUGE = "1000000000"  # GiB; any real filesystem is under this floor
WARN_ARGS = ("--warn-free-gib", HUGE, "--crit-free-gib", "0")
CRIT_ARGS = ("--warn-free-gib", HUGE, "--crit-free-gib", HUGE)
OK_ARGS = ("--warn-free-gib", "0", "--crit-free-gib", "0")


@pytest.fixture
def runner():
    return CliRunner()


def _rail_args(tmp_path, ok: bool = True) -> tuple[str, ...]:
    """CLI args for a real rail that accepts (or refuses) every message."""
    script = tmp_path / ("send-ok.sh" if ok else "send-refuse.sh")
    status, rc = ("smtp_accepted", 0) if ok else ("failed_or_unknown", 1)
    script.write_text(f'#!/bin/sh\necho \'{{"status": "{status}"}}\'\nexit {rc}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return ("--notify", "email", "--email-command", str(script))


def _run(runner, tmp_path, *args, ok: bool = True):
    result = runner.invoke(
        alarm_cmd,
        [
            "--path",
            str(tmp_path),
            "--state",
            str(tmp_path / "state.json"),
            "--json",
            *_rail_args(tmp_path, ok),
            *args,
        ],
        env={"HOME": str(tmp_path)},
    )
    return result


def _payload(result) -> dict:
    return json.loads(result.output)


def test_second_identical_warn_is_not_mailed(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    # Act
    second = _payload(_run(runner, tmp_path, *WARN_ARGS))
    # Assert
    assert second["attempted"] is False


def test_warn_after_recovery_within_the_window_is_suppressed(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    _run(runner, tmp_path, *OK_ARGS)
    # Act
    again = _payload(_run(runner, tmp_path, *WARN_ARGS))
    # Assert
    assert again["suppressed"] != [] and again["attempted"] is False


def test_zero_hour_window_mails_the_returning_warn(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    _run(runner, tmp_path, *OK_ARGS)
    # Act
    again = _payload(_run(runner, tmp_path, *WARN_ARGS, "--repeat-hours", "0"))
    # Assert
    assert again["attempted"] is True


def test_recovery_after_warn_is_mailed(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    # Act
    recovered = _payload(_run(runner, tmp_path, *OK_ARGS))
    # Assert
    assert recovered["attempted"] is True


def test_recovery_mail_names_the_filesystem(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    # Act
    recovered = _payload(_run(runner, tmp_path, *OK_ARGS))
    # Assert
    assert "recovered: warn -> ok" in recovered["text"]


def test_healthy_first_run_is_not_mailed(runner, tmp_path):
    # Arrange
    args = OK_ARGS
    # Act
    payload = _payload(_run(runner, tmp_path, *args))
    # Assert
    assert payload["attempted"] is False


def test_escalation_to_critical_is_mailed_immediately(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    # Act
    escalated = _payload(_run(runner, tmp_path, *CRIT_ARGS))
    # Assert
    assert escalated["attempted"] is True


def test_refused_push_is_retried_on_the_next_gather(runner, tmp_path):
    """A push nobody received must not be remembered as told."""
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS, ok=False)
    # Act
    retry = _payload(_run(runner, tmp_path, *WARN_ARGS))
    # Assert
    assert retry["attempted"] is True


def test_refused_push_still_exits_push_refused(runner, tmp_path):
    # Arrange
    args = WARN_ARGS
    # Act
    result = _run(runner, tmp_path, *args, ok=False)
    # Assert
    assert result.exit_code == EXIT_PUSH_REFUSED


def test_memory_is_persisted_per_filesystem(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    # Act
    memory = read_memory(tmp_path / "state.json")
    # Assert
    assert [e["announced"] for e in memory.values()] == ["warn"]


def test_state_without_memory_reads_as_empty(tmp_path):
    """A state file written before per-filesystem memory existed."""
    # Arrange
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"level": "ok", "unknown_streak": 0}))
    # Act
    memory = read_memory(path)
    # Assert
    assert memory == {}


def test_dry_run_reports_what_it_would_suppress(runner, tmp_path):
    # Arrange
    _run(runner, tmp_path, *WARN_ARGS)
    _run(runner, tmp_path, *OK_ARGS)
    # Act
    dry = _payload(_run(runner, tmp_path, *WARN_ARGS, "--dry-run"))
    # Assert
    assert dry["detail"].startswith("repeat suppressed:")


# EOF
