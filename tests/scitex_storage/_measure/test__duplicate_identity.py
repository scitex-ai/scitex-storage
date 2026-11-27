"""Real link identity controls: aliases are not extra stored copies."""

import os
import shutil

import pytest

from scitex_storage._measure._duplicates import (
    find_duplicates,
    reclaimable_bytes,
    size_groups,
)

native = pytest.mark.skipif(not shutil.which("fclones"), reason="requires native fclones")


def copies(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"independent duplicate data")
    b.write_bytes(a.read_bytes())
    return a, b


@native
def test_file_symlink_does_not_add_a_copy(tmp_path):
    # Arrange
    a, b = copies(tmp_path)
    (tmp_path / "alias.bin").symlink_to(a)
    # Act
    groups = find_duplicates([tmp_path], timeout_s=10)
    # Assert
    assert groups == [[a, b]]


@native
def test_hardlink_alias_is_reported_once_per_inode(tmp_path):
    # Arrange
    a, b = copies(tmp_path)
    os.link(a, tmp_path / "hardlink.bin")
    # Act
    groups = find_duplicates([tmp_path], timeout_s=10)
    # Assert
    assert groups == [[a, b]]


@native
def test_only_hardlink_names_are_not_a_duplicate_group(tmp_path):
    # Arrange
    a = tmp_path / "a.bin"
    a.write_bytes(b"one stored copy")
    os.link(a, tmp_path / "alias.bin")
    # Act
    result = find_duplicates([tmp_path], timeout_s=10)
    # Assert
    assert result == []


def test_hardlink_names_do_not_inflate_savings(tmp_path):
    # Arrange
    a, b = copies(tmp_path)
    alias = tmp_path / "alias.bin"
    os.link(a, alias)
    # Act
    result = reclaimable_bytes([[a, alias, b]])
    # Assert
    assert result == b.stat().st_size


def test_symbolic_alias_does_not_reclaim_target_bytes(tmp_path):
    # Arrange
    a = tmp_path / "a.bin"
    a.write_bytes(b"retained target")
    alias = tmp_path / "alias.bin"
    alias.symlink_to(a)
    # Act
    result = reclaimable_bytes([[a, alias]])
    # Assert
    assert result == 0


def test_single_link_removal_cannot_free_externally_hardlinked_copies(tmp_path):
    # Arrange
    a, b = copies(tmp_path)
    os.link(a, tmp_path / "retained-a")
    os.link(b, tmp_path / "retained-b")
    # Act
    result = reclaimable_bytes([[a, b]])
    # Assert
    assert result == 0


def test_size_candidates_deduplicate_an_explicit_symlink_root(tmp_path):
    # Arrange
    root = tmp_path / "data"
    root.mkdir()
    (root / "only.bin").write_bytes(b"one stored copy")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    # Act
    result = size_groups([root, alias])
    # Assert
    assert result == {}


def test_size_candidates_deduplicate_overlapping_roots(tmp_path):
    # Arrange
    child = tmp_path / "child"
    child.mkdir()
    (child / "only.bin").write_bytes(b"one stored copy")
    # Act
    result = size_groups([tmp_path, child])
    # Assert
    assert result == {}
