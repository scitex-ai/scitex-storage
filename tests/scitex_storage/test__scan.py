"""Unit tests for scitex_storage._scan (walk / score / dedupe)."""

import os
import time

import pytest

from scitex_storage._scan import (
    DEFAULT_EXCLUDE_DIRS,
    FileEntry,
    find_duplicates,
    scan,
    walk_tree,
)


def _touch(path, size, age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


def test_walk_tree_finds_regular_files(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    _touch(tmp_path / "sub" / "b.bin", 20)
    # Act
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Assert
    assert {e.path.name for e in entries} == {"a.bin", "b.bin"}


def test_walk_tree_skips_default_excluded_dirs(tmp_path):
    # Arrange
    _touch(tmp_path / "keep.bin", 10)
    _touch(tmp_path / ".venv" / "site" / "pkg.py", 10)
    # Act
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Assert
    assert {e.path.name for e in entries} == {"keep.bin"}


def test_walk_tree_counts_skipped_size(tmp_path):
    # Arrange
    _touch(tmp_path / "node_modules" / "pkg" / "index.js", 100)
    # Act
    _entries, _dirs, skipped_size = walk_tree(tmp_path)
    # Assert
    assert skipped_size == 100


def test_walk_tree_ignores_symlinked_files(tmp_path):
    # Arrange
    target = _touch(tmp_path / "real.bin", 10)
    (tmp_path / "link.bin").symlink_to(target)
    # Act
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Assert
    assert {e.path.name for e in entries} == {"real.bin"}


def test_default_exclude_dirs_contains_git():
    # Arrange
    excludes = DEFAULT_EXCLUDE_DIRS
    # Act
    is_excluded = ".git" in excludes
    # Assert
    assert is_excluded is True


def test_file_entry_days_since_access_for_fresh_file(tmp_path):
    # Arrange
    entry = FileEntry(path=tmp_path / "f.bin", size=100, atime=time.time())
    # Act
    days = entry.days_since_access()
    # Assert
    assert days < 1


def test_file_entry_days_since_access_for_old_file(tmp_path):
    # Arrange
    old_atime = time.time() - 100 * 86400
    entry = FileEntry(path=tmp_path / "f.bin", size=100, atime=old_atime)
    # Act
    days = entry.days_since_access()
    # Assert
    assert days == pytest.approx(100, abs=1)


def test_file_entry_score_is_size_times_days():
    # Arrange
    now = time.time()
    entry = FileEntry(path=None, size=1000, atime=now - 10 * 86400)
    # Act
    score = entry.score(now)
    # Assert
    assert score == pytest.approx(10000, rel=0.01)


def test_scan_reports_total_size(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 100)
    _touch(tmp_path / "b.bin", 200)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.total_size == 300


def test_scan_top_candidates_orders_by_score_descending(tmp_path):
    # Arrange
    _touch(tmp_path / "small_stale.bin", 10, age_days=900)
    _touch(tmp_path / "big_stale.bin", 10_000, age_days=900)
    # Act
    result = scan(tmp_path)
    top = result.top_candidates(top=2)
    # Assert
    assert top[0].path.name == "big_stale.bin"


def test_scan_raises_for_missing_directory(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        scan(missing)


def test_scan_is_read_only(tmp_path):
    # Arrange
    f = _touch(tmp_path / "a.bin", 10)
    before = f.read_bytes()
    # Act
    scan(tmp_path)
    # Assert
    assert f.read_bytes() == before


def test_find_duplicates_groups_identical_files(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 50)
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"x" * 50)
    (tmp_path / "c.bin").write_bytes(b"y" * 50)
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Act
    groups = find_duplicates(entries)
    # Assert
    assert any(len(g) == 2 for g in groups)


def test_find_duplicates_ignores_different_content_same_size(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"y" * 50)
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Act
    groups = find_duplicates(entries)
    # Assert
    assert groups == []


def test_find_duplicates_ignores_empty_files(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"")
    (tmp_path / "b.bin").write_bytes(b"")
    entries, _dirs, _skipped = walk_tree(tmp_path)
    # Act
    groups = find_duplicates(entries)
    # Assert
    assert groups == []


def test_scan_dedupe_false_skips_duplicate_pass(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"x" * 50)
    # Act
    result = scan(tmp_path, dedupe=False)
    # Assert
    assert result.duplicate_groups == []
