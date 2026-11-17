"""Backup verification measures the population the transport sends.

Regression for 2026-11-16 (business-repo backup to scitex-nas-01/02):
1. `backup --exclude .worktrees` counted the excluded trees in the baseline,
   so a complete copy failed verification.
2. The remote tally used GNU `find -printf`; BusyBox find (QNAP) printed
   nothing, which was read as "0 entries".

NO MOCKS (PA-306): real temp trees; the remote tally command is run for real
through `sh`, exactly as the remote shell runs it.
"""

import subprocess

import pytest

from scitex_storage._transfer._content_verify import digest_tree
from scitex_storage._transfer._verify import (
    REMOTE_TALLY_CMD,
    is_excluded,
    local_tally,
    parse_remote_tally,
)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "src"
    for rel in [
        "a.txt",
        "docs/b.txt",
        ".worktrees/x/c.txt",
        "pkg/.venv/d.txt",
        "logs/e.log",
    ]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel)
    return root


def test_basename_pattern_matches_at_any_depth():
    # Arrange
    patterns = (".venv",)
    # Act
    hit = is_excluded("pkg/.venv/d.txt", patterns)
    # Assert
    assert hit is True


def test_anchored_pattern_matches_only_at_root():
    # Arrange
    patterns = ("/logs",)
    # Act
    hit = is_excluded("deep/logs/e.log", patterns)
    # Assert
    assert hit is False


def test_trailing_slash_pattern_is_accepted():
    # Arrange
    patterns = (".worktrees/",)
    # Act
    hit = is_excluded(".worktrees/x/c.txt", patterns)
    # Assert
    assert hit is True


def test_local_tally_skips_excluded_trees(tree):
    # Arrange
    exclude = (".worktrees", ".venv")
    # Act
    tally = local_tally(str(tree), exclude=exclude)
    # Assert
    assert tally.entry_count == 3


def test_local_tally_without_exclude_counts_everything(tree):
    # Arrange
    # Act
    tally = local_tally(str(tree))
    # Assert
    assert tally.entry_count == 5


def test_digest_tree_skips_excluded_trees(tree):
    # Arrange
    exclude = (".worktrees", ".venv")
    # Act
    manifest = digest_tree(str(tree), exclude=exclude)
    # Assert
    assert sorted(manifest.digests) == ["a.txt", "docs/b.txt", "logs/e.log"]


def test_remote_tally_command_counts_like_local_tally(tree):
    # Arrange -- run the exact remote command through a real shell.
    cmd = REMOTE_TALLY_CMD.format(path=str(tree))
    # Act
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True)
    # Assert
    assert (
        parse_remote_tally(out.stdout).entry_count == local_tally(str(tree)).entry_count
    )


def test_remote_tally_command_does_not_use_gnu_printf():
    # Arrange -- BusyBox find (QNAP) has no -printf; it must never come back.
    # Act
    uses_printf = "-printf" in REMOTE_TALLY_CMD
    # Assert
    assert uses_printf is False
