"""Actual Event/monotonic guards stop grouping before inaccessible work."""

import threading
import time

import pytest

from scitex_storage import group_duplicates


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_expired_deadline_refuses_before_source_or_root_inspection(tmp_path, by):
    # Arrange
    missing = tmp_path / "missing"
    # Act
    # Assert
    with pytest.raises(TimeoutError, match="deadline"):
        group_duplicates(
            [[missing]], by=by, roots=[missing], deadline=time.monotonic() - 1
        )


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_cancelled_event_refuses_before_source_or_root_inspection(tmp_path, by):
    # Arrange
    missing = tmp_path / "missing"
    event = threading.Event()
    event.set()
    # Act
    # Assert
    with pytest.raises(TimeoutError, match="cancelled"):
        group_duplicates([[missing]], by=by, roots=[missing], cancelled=event.is_set)


@pytest.mark.parametrize(
    "deadline", [True, "soon", float("inf"), float("nan"), 10**1000]
)
def test_invalid_deadline_is_refused(deadline):
    # Arrange
    groups = []
    # Act
    # Assert
    with pytest.raises(ValueError, match="finite monotonic"):
        group_duplicates(groups, deadline=deadline)


def test_non_callable_cancellation_is_refused():
    # Arrange
    groups = []
    # Act
    # Assert
    with pytest.raises(TypeError, match="callable predicate"):
        group_duplicates(groups, cancelled=True)


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_active_budget_preserves_regular_grouping_results(tmp_path, by):
    # Arrange
    paths = [tmp_path / "a", tmp_path / "b"]
    for path in paths:
        path.write_bytes(b"same")
    event = threading.Event()
    expected = group_duplicates([paths], by=by, roots=[tmp_path])
    # Act
    actual = group_duplicates(
        [paths],
        by=by,
        roots=[tmp_path],
        deadline=time.monotonic() + 30,
        cancelled=event.is_set,
    )
    # Assert
    assert actual == expected


def test_cancelled_empty_input_does_not_look_like_success():
    # Arrange
    event = threading.Event()
    event.set()
    # Act
    # Assert
    with pytest.raises(TimeoutError):
        group_duplicates([], cancelled=event.is_set)
