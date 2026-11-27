"""Tests for the remote target verifier's refusal paths (no transport reached).

The positive path, and a changed remote copy, run with real local rsync and
sha256sum in tests/integration/test_tier_roundtrip.py.
"""

import time

from scitex_storage._transfer._remote_target import remote_target_verifier


def _never_called(command, **kwargs):
    raise AssertionError("the verifier must refuse before reaching the transport")


def _call(target, *, deadline_in=30.0):
    verify = remote_target_verifier(runner=_never_called)
    return verify(
        target,
        deadline=time.monotonic() + deadline_in,
        cancelled=lambda: False,
        max_bytes=1_000,
        max_entries=10,
    )


def test_local_target_is_not_this_verifiers_business():
    # Arrange
    target = {"kind": "local", "path": "/somewhere"}
    # Act
    verdict = _call(target)
    # Assert
    assert verdict is False


def test_expired_deadline_is_refused():
    # Arrange
    target = {"kind": "remote", "host": "scitex-nas-02", "manifest_path": "/x"}
    # Act
    verdict = _call(target, deadline_in=-1.0)
    # Assert
    assert verdict is False


def test_unreadable_receipt_is_refused(tmp_path):
    # Arrange
    target = {
        "kind": "remote",
        "host": "scitex-nas-02",
        "path": "/remote/x",
        "manifest_path": str(tmp_path / "missing.json"),
    }
    # Act
    verdict = _call(target)
    # Assert
    assert verdict is False
