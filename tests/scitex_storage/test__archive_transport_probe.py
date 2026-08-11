#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for probe_transport (can the archive transport connect?).

NO MOCKS (PA-306): uses the module's own ``runner`` seam -- the same injection
point ``apply_archive``/``apply_restore`` already expose for their fake-runner
tests -- so these exercise the real code path with a real callable, not a
patched import. ONE assertion per test (PA-307), AAA-structured.
"""

import subprocess

from scitex_storage._archive_transport import (
    TRANSPORT_COULD_NOT_LOOK,
    TRANSPORT_REACHABLE,
    TRANSPORT_UNREACHABLE,
    probe_transport,
)


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(returncode=0, stdout="", stderr=""):
    def run(argv, **kwargs):
        run.argv = argv
        return _Result(returncode, stdout, stderr)

    return run


def test_probe_reports_reachable_on_exit_zero():
    # Arrange
    run = _runner(returncode=0)
    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert probe.verdict == TRANSPORT_REACHABLE


def test_probe_reports_unreachable_on_nonzero_exit():
    # Arrange -- the real 2026-08-11 failure: ssh aborts before connecting.
    run = _runner(returncode=255, stderr="unix_listener: cannot bind to path ...")
    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert probe.verdict == TRANSPORT_UNREACHABLE


def test_probe_carries_the_remote_stderr_verbatim():
    # Arrange -- the exact wording IS the repair: a bind error, a host-key
    # refusal and an auth failure need three different fixes, and a summarised
    # "connection failed" tells the caller which of them it is: none.
    run = _runner(returncode=255, stderr="Permission denied (publickey).")
    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert "Permission denied (publickey)." in probe.detail


def test_probe_reports_could_not_look_on_timeout():
    # Arrange -- a probe that never returned is NOT a host that refused. This
    # is the third state; collapsing it into either pole is the bug this
    # package keeps finding in other people's instruments.
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert probe.verdict == TRANSPORT_COULD_NOT_LOOK


def test_could_not_look_does_not_grant_permission_to_transport():
    # Arrange -- may_transport must be TRUE only on a positive result. A
    # timeout that read as "fine" would be worse than no probe at all.
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert probe.may_transport is False


def test_probe_reports_could_not_look_when_ssh_cannot_be_run():
    # Arrange -- no ssh binary at all is another could-not-look, not a refusal.
    def run(argv, **kwargs):
        raise OSError("No such file or directory: 'ssh'")

    # Act
    probe = probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert probe.verdict == TRANSPORT_COULD_NOT_LOOK


def test_probe_asks_the_named_destination():
    # Arrange -- guards against probing a hardcoded or stale host, which would
    # answer a question about a different machine than the one being archived to.
    run = _runner(returncode=0)
    # Act
    probe_transport("scitex-nas-03", runner=run)
    # Assert
    assert "scitex-nas-03" in run.argv


def test_probe_runs_a_harmless_remote_command():
    # Arrange -- the probe must not mutate the destination it is testing.
    run = _runner(returncode=0)
    # Act
    probe_transport("scitex-nas-01", runner=run)
    # Assert
    assert run.argv[-1] == "true"
