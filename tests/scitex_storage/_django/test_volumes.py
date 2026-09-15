"""Tests for scitex_storage._django.volumes: measuring, containment, listing.

Real temp directories; no mocks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("django")

from scitex_storage._django import volumes  # noqa: E402


def _vol(path) -> volumes.Volume:
    return volumes.Volume(key="k", label="K", path=Path(path), machine="m")


def test_measure_reports_capacity_for_an_existing_directory(tmp_path):
    # Arrange
    vol = _vol(tmp_path)
    # Act
    status = volumes.measure(vol)
    # Assert
    assert status.status == volumes.REACHABLE and status.total > 0


def test_measure_reports_not_mounted_for_a_missing_directory(tmp_path):
    # Arrange
    vol = _vol(tmp_path / "absent")
    # Act
    status = volumes.measure(vol)
    # Assert
    assert status.status == volumes.NOT_MOUNTED


def test_with_deadline_gives_up_on_a_hung_probe():
    # Arrange
    import threading

    gate = threading.Event()
    # Act
    done, _result, _err = volumes.with_deadline(gate.wait, timeout=0.05)
    gate.set()
    # Assert
    assert done is False


def test_contained_dir_rejects_parent_traversal(tmp_path):
    # Arrange
    vol = _vol(tmp_path)
    # Act
    call = lambda: volumes.contained_dir(vol, "../..")  # noqa: E731
    # Assert
    with pytest.raises(volumes.OutsideVolume):
        call()


def test_contained_dir_rejects_a_symlink_out_of_the_volume(tmp_path):
    # Arrange
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(tmp_path, root / "escape")
    vol = _vol(root)
    # Act
    call = lambda: volumes.contained_dir(vol, "escape")  # noqa: E731
    # Assert
    with pytest.raises(volumes.OutsideVolume):
        call()


def test_absolute_dir_is_treated_as_relative_to_the_volume(tmp_path):
    # Arrange
    vol = _vol(tmp_path)
    # Act
    target = volumes.contained_dir(vol, "/etc")
    # Assert
    assert target == (tmp_path / "etc").resolve()


def test_list_dir_puts_folders_before_files(tmp_path):
    # Arrange
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a_dir").mkdir()
    # Act
    listing = volumes.list_dir(_vol(tmp_path))
    # Assert
    assert [e.name for e in listing.entries] == ["a_dir", "b.txt"]


def test_list_dir_reports_a_missing_folder(tmp_path):
    # Arrange
    vol = _vol(tmp_path)
    # Act
    listing = volumes.list_dir(vol, "nope")
    # Assert
    assert listing.error == "missing"


# EOF
