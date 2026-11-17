"""Incomplete source inspection cannot produce a successful reclaim plan."""

import pytest

from scitex_storage._transfer._reclaim import _measure, plan_reclaim


@pytest.fixture(params=["missing", "not-directory", "parent-loop"])
def invalid_source(tmp_path, request):
    if request.param == "missing":
        return tmp_path / "absent"
    if request.param == "not-directory":
        parent = tmp_path / "file"
        parent.write_bytes(b"payload")
        return parent / "child"
    parent = tmp_path / "loop"
    parent.symlink_to(parent.name)
    return parent / "child"


def test_measurement_refuses_uninspectable_sources(invalid_source):
    # Arrange
    source = invalid_source
    # Act
    # Assert
    with pytest.raises(OSError):
        _measure(source)


def test_failed_measurement_creates_no_archive(invalid_source, tmp_path):
    # Arrange
    source = invalid_source
    before = sorted(path.name for path in tmp_path.iterdir())
    # Act
    try:
        _measure(source)
    except OSError:
        pass
    # Assert
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_empty_source_is_still_a_valid_zero_plan(tmp_path):
    # Arrange
    source = tmp_path / "empty"
    source.mkdir()
    # Act
    plan = plan_reclaim([source], run_id="empty")
    # Assert
    assert (plan.total_size, plan.total_files, (tmp_path / ".old").exists()) == (
        0,
        0,
        False,
    )


def test_readable_source_plan_is_stat_only_and_read_only(tmp_path):
    # Arrange
    root = tmp_path / "root"
    root.mkdir()
    content = root / "data.bin"
    content.write_bytes(b"payload")
    # Act
    plan = plan_reclaim([root], run_id="readable")
    # Assert
    assert (
        plan.total_size,
        plan.total_files,
        content.read_bytes(),
        (tmp_path / ".old").exists(),
    ) == (7, 1, b"payload", False)
