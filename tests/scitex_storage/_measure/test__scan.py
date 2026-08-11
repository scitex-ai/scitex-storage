"""Unit tests for scitex_storage._scan (per-top-level-child size + inode scan).

`_measure_dir`'s walk shells out to `fd` (see _scan.py's module docstring
for the multi-terabyte-scale rationale) -- every test above the
"fd dependency" section exercises the PUBLIC `scan`/`scan_roots` contract
and is agnostic to that implementation detail (they pass identically
whether the walk is os.walk-based or fd-based), so they are unchanged from
before the fd migration. The dependency-handling tests below are new.
"""

import os
import shutil

import os

import pytest

from scitex_storage._measure._scan import ChildUsage, MissingSystemDependencyError, RootScan, scan, scan_roots

# Resolved once at collection time -- BEFORE any test's isolated_path_bin_dir
# fixture swaps PATH out from under a later `shutil.which()` call.
_REAL_FD_BIN = shutil.which("fd") or shutil.which("fdfind")


def _touch(path, size=1, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_scan_reports_one_child_per_top_level_entry(tmp_path):
    # Arrange
    _touch(tmp_path / "alpha" / "a.bin", 10)
    _touch(tmp_path / "beta" / "b.bin", 20)
    # Act
    result = scan(tmp_path)
    # Assert
    assert {c.name for c in result.children} == {"alpha", "beta"}


def test_scan_sums_size_recursively_under_a_child(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 100)
    _touch(tmp_path / "child" / "sub" / "b.bin", 200)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].size == 300


def test_scan_counts_inodes_recursively_under_a_child(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 1)
    _touch(tmp_path / "child" / "sub" / "b.bin", 1)
    _touch(tmp_path / "child" / "sub" / "c.bin", 1)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].file_count == 3


def test_scan_top_level_file_child_is_not_a_directory(tmp_path):
    # Arrange
    _touch(tmp_path / "loose.bin", 42)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].is_dir is False


def test_scan_top_level_file_child_counts_as_one_inode(tmp_path):
    # Arrange
    _touch(tmp_path / "loose.bin", 42)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].file_count == 1


def test_scan_symlinked_directory_child_counts_as_one_inode(tmp_path):
    # Arrange
    real = tmp_path / "real"
    _touch(real / "a.bin", 10)
    _touch(real / "b.bin", 10)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    # Act
    result = scan(tmp_path)
    # Assert
    link_child = next(c for c in result.children if c.name == "link")
    assert link_child.file_count == 1


def test_scan_symlinked_directory_child_is_not_reported_as_directory(tmp_path):
    # Arrange
    real = tmp_path / "real"
    _touch(real / "a.bin", 10)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    # Act
    result = scan(tmp_path)
    # Assert
    link_child = next(c for c in result.children if c.name == "link")
    assert link_child.is_dir is False


def test_scan_does_not_follow_symlinked_directory_nested(tmp_path):
    # Arrange
    outside = tmp_path / "outside"
    _touch(outside / "x.bin", 10)
    _touch(outside / "y.bin", 10)
    child = tmp_path / "child"
    child.mkdir()
    _touch(child / "own.bin", 5)
    (child / "escape").symlink_to(outside, target_is_directory=True)
    # Act
    result = scan(tmp_path)
    # Assert — child's own file counts; the escape symlink is never traversed
    child_usage = next(c for c in result.children if c.name == "child")
    assert child_usage.file_count == 1


def test_scan_max_depth_bounds_recursion(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 1)  # depth 0 under child
    _touch(tmp_path / "child" / "x" / "b.bin", 1)  # depth 1
    _touch(tmp_path / "child" / "x" / "y" / "c.bin", 1)  # depth 2
    # Act
    result = scan(tmp_path, max_depth=1)
    # Assert — a.bin + b.bin counted, c.bin (depth 2) excluded
    assert result.children[0].file_count == 2


def test_scan_raises_for_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        scan(missing)


def test_scan_raises_for_non_directory_path(tmp_path):
    # Arrange
    a_file = _touch(tmp_path / "a.bin", 10)
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        scan(a_file)


def test_scan_is_read_only(tmp_path):
    # Arrange
    f = _touch(tmp_path / "child" / "a.bin", 10)
    before = f.read_bytes()
    # Act
    scan(tmp_path)
    # Assert
    assert f.read_bytes() == before


def test_rootscan_total_size_aggregates_children(tmp_path):
    # Arrange
    _touch(tmp_path / "a" / "f.bin", 100)
    _touch(tmp_path / "b" / "g.bin", 250)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.total_size == 350


def test_rootscan_total_files_aggregates_children(tmp_path):
    # Arrange
    _touch(tmp_path / "a" / "f.bin", 1)
    _touch(tmp_path / "b" / "g.bin", 1)
    _touch(tmp_path / "b" / "h.bin", 1)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.total_files == 3


def test_rootscan_by_size_orders_biggest_first(tmp_path):
    # Arrange
    _touch(tmp_path / "small" / "s.bin", 10)
    _touch(tmp_path / "large" / "l.bin", 10_000)
    # Act
    result = scan(tmp_path)
    ordered = result.by_size()
    # Assert
    assert ordered[0].name == "large"


def test_rootscan_by_file_count_orders_most_inodes_first(tmp_path):
    # Arrange
    _touch(tmp_path / "few" / "a.bin", 5_000)  # big but 1 inode
    for i in range(5):
        _touch(tmp_path / "many" / f"f{i}.bin", 1)  # tiny but 5 inodes
    # Act
    result = scan(tmp_path)
    ordered = result.by_file_count()
    # Assert
    assert ordered[0].name == "many"


def test_scan_roots_returns_one_result_per_root(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    _touch(root_a / "a.bin", 10)
    _touch(root_b / "b.bin", 20)
    # Act
    results = scan_roots([root_a, root_b])
    # Assert
    assert [r.root.name for r in results] == ["A", "B"]


def test_scan_roots_returns_rootscan_instances(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    _touch(root_a / "a.bin", 10)
    # Act
    results = scan_roots([root_a])
    # Assert
    assert isinstance(results[0], RootScan)


def test_scan_child_is_a_childusage_instance(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    # Act
    child = scan(tmp_path).children[0]
    # Assert
    assert isinstance(child, ChildUsage)


def test_scan_newest_mtime_reflects_the_most_recently_modified_file(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "old.bin", mtime=1_000_000)
    _touch(tmp_path / "child" / "new.bin", mtime=2_000_000)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].newest_mtime == 2_000_000


def test_scan_newest_mtime_for_top_level_file_is_its_own_mtime(tmp_path):
    # Arrange
    _touch(tmp_path / "loose.bin", mtime=1_500_000)
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].newest_mtime == 1_500_000


def test_scan_newest_mtime_falls_back_to_directory_mtime_when_empty(tmp_path):
    # Arrange
    child = tmp_path / "empty"
    child.mkdir()
    os.utime(child, (1_234_567, 1_234_567))
    # Act
    result = scan(tmp_path)
    # Assert
    assert result.children[0].newest_mtime == 1_234_567
# =============================================================================
# fd dependency -- missing-binary error handling
#
# This package forbids `monkeypatch`/`mocker` pytest fixtures ecosystem-wide
# (STX-NM002 -- "production talks to the real collaborator"), so PATH is
# isolated via a real env-var mutation (yield-based fixture) instead: the
# code under test still calls the real `shutil.which` / real
# `subprocess.run`; this only controls which directory those real stdlib
# calls can see.
# =============================================================================


@pytest.fixture
def isolated_path_bin_dir(tmp_path):
    """Replace PATH with a fresh, empty directory for the test's duration."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    original_path = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    yield bin_dir
    os.environ["PATH"] = original_path


def test_scan_raises_missing_dependency_when_fd_absent(tmp_path, isolated_path_bin_dir):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    # Act
    # Assert
    with pytest.raises(MissingSystemDependencyError, match="cargo install fd-find"):
        scan(tmp_path)


@pytest.mark.skipif(
    _REAL_FD_BIN is None, reason="requires a real `fd`/`fdfind` binary on PATH"
)
def test_scan_finds_fd_under_its_debian_fdfind_alias(tmp_path, isolated_path_bin_dir):
    """`fd-find`'s apt package installs the binary as `fdfind`, not `fd`."""
    # Arrange -- scan a dedicated subdir, not tmp_path itself, so the fake
    # `bin/` the fixture created inside tmp_path isn't itself a scanned child.
    (isolated_path_bin_dir / "fdfind").symlink_to(_REAL_FD_BIN)
    target = tmp_path / "target"
    _touch(target / "child" / "a.bin", 10)
    # Act
    result = scan(target)
    # Assert
    assert result.children[0].name == "child"


# EOF
