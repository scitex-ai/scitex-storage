"""Real cancellation and path failures keep report/savings verdicts distinct."""

import threading

import pytest

from scitex_storage._django._duplicate_report import _build_duplicate_report
from scitex_storage._measure._duplicate_identity import reclaimable_bytes


def test_cancelled_report_refuses_before_hashing_missing_roots(tmp_path):
    # Arrange
    event = threading.Event()
    event.set()
    # Act
    # Assert
    with pytest.raises(TimeoutError):
        _build_duplicate_report(
            [tmp_path / "missing"],
            "parent",
            str,
            depth=4,
            timeout_s=60,
            cancelled=event.is_set,
        )


def test_savings_cancellation_is_not_reported_as_unknown(tmp_path):
    # Arrange
    path = tmp_path / "data"
    path.write_bytes(b"data")
    event = threading.Event()
    event.set()

    def checkpoint():
        if event.is_set():
            raise TimeoutError("cancelled")

    # Act
    # Assert
    with pytest.raises(TimeoutError):
        reclaimable_bytes([[path]], checkpoint=checkpoint)


def test_filesystem_inspection_error_remains_unknown_with_checkpoints(tmp_path):
    # Arrange
    parent = tmp_path / "file"
    parent.write_bytes(b"data")
    event = threading.Event()

    def checkpoint():
        if event.is_set():
            raise TimeoutError("cancelled")

    # Act
    result = reclaimable_bytes([[parent / "child"]], checkpoint=checkpoint)
    # Assert
    assert result is None
