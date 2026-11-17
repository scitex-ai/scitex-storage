"""Capacity estimates cannot turn inaccessible trees into measured zero bytes."""

import pytest

from scitex_storage._transfer._reclaim import _tree_size
from scitex_storage._transfer._sweep import check_space


@pytest.fixture(params=["missing", "not-directory", "parent-loop"])
def uninspectable_path(tmp_path, request):
    if request.param == "missing":
        return tmp_path / "absent"
    if request.param == "not-directory":
        parent = tmp_path / "file"
        parent.write_bytes(b"payload")
        return parent / "child"
    parent = tmp_path / "loop"
    parent.symlink_to(parent.name)
    return parent / "child"


def test_uninspectable_size_produces_unknown_capacity(uninspectable_path):
    # Arrange
    available = 1024
    # Act
    verdict = check_space(_tree_size(uninspectable_path), available)
    # Assert
    assert (verdict.ok, verdict.needed, verdict.available) == (None, None, available)


def test_empty_tree_remains_a_measured_zero(tmp_path):
    # Arrange
    empty = tmp_path / "empty"
    empty.mkdir()
    # Act
    verdict = check_space(_tree_size(empty), 1024)
    # Assert
    assert (verdict.ok, verdict.needed) == (True, 0)


def test_file_and_nested_tree_measurement_remain_exact(tmp_path):
    # Arrange
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    first = root / "first.bin"
    first.write_bytes(b"abcd")
    (nested / "second.bin").write_bytes(b"xyz")
    # Act
    observed = (_tree_size(first), _tree_size(root))
    # Assert
    assert observed == (4, 7)


@pytest.fixture(params=["file", "directory", "broken"])
def link_case(tmp_path, request):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    if request.param == "file":
        target.write_bytes(b"target content is not copied through the link")
    elif request.param == "directory":
        target.mkdir()
        (target / "content.bin").write_bytes(b"external directory content")
    link = root / "link"
    link.symlink_to(target, target_is_directory=request.param == "directory")
    return root, link


def test_links_measure_the_link_without_following_target(link_case):
    # Arrange
    root, link = link_case
    link_bytes = link.lstat().st_size
    # Act
    observed = (_tree_size(link), _tree_size(root))
    # Assert
    assert observed == (link_bytes, link_bytes)
