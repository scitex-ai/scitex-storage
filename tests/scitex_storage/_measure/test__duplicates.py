"""Unit + integration tests for scitex_storage._measure._duplicates (fclones-backed).

Layered like test__scan.py's fd-dependency section: pure-logic / JSON-
protocol tests control PATH via a real env-var mutation (this repo forbids
`monkeypatch`/`mocker` fixtures ecosystem-wide, STX-NM002) and, where a
fake collaborator is needed, write a real, executable fake `fclones`
script into the isolated PATH directory. Integration tests exercise the
real `fclones` binary and are skipped (with a clear reason) when it isn't
on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys

import pytest

from scitex_storage._measure._duplicates import find_duplicates
from scitex_storage._measure._scan import MissingSystemDependencyError

_HAVE_FCLONES = bool(shutil.which("fclones"))
requires_fclones = pytest.mark.skipif(
    not _HAVE_FCLONES, reason="requires the `fclones` binary on PATH"
)

# Fake-`fclones` scripts need a real interpreter in their shebang; `/usr/bin/
# env python3` would depend on PATH, which the isolated PATH fixture
# deliberately empties out, so use this process's own interpreter instead.
_PYTHON3_BIN = sys.executable


def _write_executable(path, body: str) -> None:
    path.write_text(f"#!{_PYTHON3_BIN}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def isolated_path_bin_dir(tmp_path):
    """Replace PATH with a fresh, empty directory for the test's duration."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    original_path = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    yield bin_dir
    os.environ["PATH"] = original_path


# =============================================================================
# Validation (no binary needed -- fails before resolving fclones)
# =============================================================================


def test_find_duplicates_returns_empty_list_for_no_roots():
    # Arrange
    roots = []
    # Act
    groups = find_duplicates(roots)
    # Assert
    assert groups == []


def test_find_duplicates_raises_for_missing_root(tmp_path):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        find_duplicates([missing])


def test_find_duplicates_raises_for_non_directory_root(tmp_path):
    # Arrange
    a_file = tmp_path / "a.bin"
    a_file.write_bytes(b"x")
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        find_duplicates([a_file])


# =============================================================================
# Missing-binary error handling (real PATH control, no fake binary)
# =============================================================================


def test_find_duplicates_raises_missing_dependency_when_fclones_absent(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    # (isolated_path_bin_dir is empty: `fclones` does not resolve)
    # Act
    # Assert
    with pytest.raises(MissingSystemDependencyError, match="cargo install fclones"):
        find_duplicates([tmp_path])


# =============================================================================
# fclones JSON protocol (real subprocess, fake `fclones`)
# =============================================================================

_FAKE_FCLONES_ONE_GROUP = """\
import json
print(json.dumps({
    "header": {"stats": {}},
    "groups": [{"file_len": 1, "file_hash": "fakehash", "files": ["/a", "/b"]}],
}))
"""

_FAKE_FCLONES_ALWAYS_FAILS = """\
import sys
print("boom", file=sys.stderr)
sys.exit(2)
"""


def test_find_duplicates_parses_fclones_json_groups(tmp_path, isolated_path_bin_dir):
    # Arrange
    _write_executable(isolated_path_bin_dir / "fclones", _FAKE_FCLONES_ONE_GROUP)
    # Act
    groups = find_duplicates([tmp_path])
    # Assert
    assert sorted(str(p) for p in groups[0]) == ["/a", "/b"]


def test_find_duplicates_raises_runtime_error_on_fclones_nonzero_exit(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    _write_executable(isolated_path_bin_dir / "fclones", _FAKE_FCLONES_ALWAYS_FAILS)
    # Act
    # Assert
    with pytest.raises(RuntimeError, match="boom"):
        find_duplicates([tmp_path])


def test_find_duplicates_passes_max_depth_to_fclones(tmp_path, isolated_path_bin_dir):
    # Arrange
    captured = tmp_path / "captured_argv.json"
    script = f"""\
import json, sys
with open({str(captured)!r}, "w") as f:
    json.dump(sys.argv[1:], f)
print(json.dumps({{"header": {{"stats": {{}}}}, "groups": []}}))
"""
    _write_executable(isolated_path_bin_dir / "fclones", script)
    # Act
    find_duplicates([tmp_path], max_depth=3)
    # Assert
    assert "--depth" in json.loads(captured.read_text())


# =============================================================================
# Integration: real `fclones` (skipped when not installed)
# =============================================================================


@requires_fclones
def test_find_duplicates_groups_identical_files(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"x" * 50)
    (tmp_path / "c.bin").write_bytes(b"y" * 50)
    # Act
    groups = find_duplicates([tmp_path])
    # Assert
    assert any(len(g) == 2 for g in groups)


@requires_fclones
def test_find_duplicates_ignores_different_content_same_size(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"y" * 50)
    # Act
    groups = find_duplicates([tmp_path])
    # Assert
    assert groups == []


@requires_fclones
def test_find_duplicates_is_read_only(tmp_path):
    # Arrange
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 50)
    (tmp_path / "b.bin").write_bytes(b"x" * 50)
    before = f.read_bytes()
    # Act
    find_duplicates([tmp_path])
    # Assert
    assert f.read_bytes() == before


@requires_fclones
def test_find_duplicates_across_multiple_roots(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    (root_a / "x.bin").parent.mkdir(parents=True, exist_ok=True)
    (root_a / "x.bin").write_bytes(b"same" * 20)
    (root_b / "y.bin").parent.mkdir(parents=True, exist_ok=True)
    (root_b / "y.bin").write_bytes(b"same" * 20)
    # Act
    groups = find_duplicates([root_a, root_b])
    # Assert
    assert any(len(g) == 2 for g in groups)


# =============================================================================
# Staged dedupe: size_groups (stat-only) + overlap_bytes (pure logic)
# =============================================================================


def test_size_groups_finds_candidates_across_roots(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    (root_a / "x.bin").parent.mkdir(parents=True, exist_ok=True)
    (root_a / "x.bin").write_bytes(b"same" * 20)
    (root_b / "y.bin").parent.mkdir(parents=True, exist_ok=True)
    (root_b / "y.bin").write_bytes(b"same" * 20)
    (root_b / "unique.bin").write_bytes(b"different-length")
    # Act
    from scitex_storage._measure._duplicates import (
        candidate_bytes,
        size_groups,
    )

    by_size = size_groups([root_a, root_b])
    # Assert
    assert list(by_size) == [80]
    assert sorted(p.name for p in by_size[80]) == ["x.bin", "y.bin"]
    assert candidate_bytes(by_size) == 80


def test_size_groups_empty_without_shared_sizes(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x")
    (tmp_path / "b.bin").write_bytes(b"xyz")
    # Act
    from scitex_storage._measure._duplicates import (
        candidate_bytes,
        size_groups,
    )

    by_size = size_groups([tmp_path])
    # Assert
    assert by_size == {}
    assert candidate_bytes(by_size) is None


def test_size_groups_respects_max_depth(tmp_path):
    # Arrange
    (tmp_path / "top.bin").write_bytes(b"12345678")
    deep = tmp_path / "sub" / "deep"
    deep.mkdir(parents=True)
    (deep / "deep.bin").write_bytes(b"12345678")
    # Act
    from scitex_storage._measure._duplicates import size_groups

    # Assert
    assert size_groups([tmp_path], max_depth=1) == {}
    assert 8 in size_groups([tmp_path])


def test_size_groups_raises_for_bad_root(tmp_path):
    # Arrange
    from scitex_storage._measure._duplicates import size_groups

    not_a_dir = tmp_path / "f.bin"
    not_a_dir.write_bytes(b"x")
    # Act / Assert
    with pytest.raises(FileNotFoundError):
        size_groups([tmp_path / "missing"])
    with pytest.raises(NotADirectoryError):
        size_groups([not_a_dir])


def test_overlap_bytes_reports_cross_root_duplicates(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    for d in (root_a, root_b):
        d.mkdir()
    a1 = root_a / "a1.bin"
    a1.write_bytes(b"v" * 100)
    a2 = root_a / "a2.bin"
    a2.write_bytes(b"v" * 100)
    b1 = root_b / "b1.bin"
    b1.write_bytes(b"v" * 100)
    c = root_b / "c.bin"
    c.write_bytes(b"other" * 10)
    # Act
    from scitex_storage._measure._duplicates import overlap_bytes

    matrix = overlap_bytes([[a1, a2, b1], [c]], [root_a, root_b])
    # Assert
    a, b = str(root_a.resolve()), str(root_b.resolve())
    assert matrix[a][b] == 200  # both A files exist under B
    assert matrix[b][a] == 100  # one B file exists under A
    assert matrix[a][a] == 100  # one redundant copy strictly inside A
    assert matrix[b][b] == 0


# EOF
