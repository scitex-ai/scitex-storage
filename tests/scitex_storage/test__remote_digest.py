#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: tests/scitex_storage/test__remote_digest.py
"""Tests for remote content digesting.

THE COMMAND IS EXERCISED FOR REAL, not only its parser. `sh -c` against a
temp dir is the closest thing to the NAS this container can reach, and it
catches the class that matters most here: a shell string that is syntactically
wrong, or that depends on a GNU extension, produces EMPTY OUTPUT and rc=0 in
enough shells to look like an empty tree.
"""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from scitex_storage._content_verify import digest_file, digest_tree, verify_content
from scitex_storage._remote_digest import (
    MISSING_ROOT_MARKER,
    REMOTE_DIGEST_CMD,
    UNREADABLE_MARKER,
    local_symlink_digest,
    parse_remote_manifest,
)
from scitex_storage._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED


def _run(path):
    """Execute the remote command locally under /bin/sh."""
    cmd = REMOTE_DIGEST_CMD.format(path=shlex.quote(str(path)))
    return subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True)


def _write(root, rel, data):
    full = os.path.join(str(root), rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "t"
    _write(root, "a.txt", b"alpha")
    _write(root, "sub/b.bin", b"\x01\x02\x03" * 10)
    return root


# --------------------------------------------------------------------------
# the command actually runs -- positive control
# --------------------------------------------------------------------------
def test_the_command_exits_zero(tree):
    # Arrange
    path = tree
    # Act
    result = _run(path)
    # Assert
    assert result.returncode == 0


def test_the_command_emits_a_line_per_entry(tree):
    # Arrange
    path = tree
    # Act
    lines = [ln for ln in _run(path).stdout.splitlines() if ln.strip()]
    # Assert
    assert len(lines) == 2


def test_the_remote_digests_match_the_local_ones(tree):
    """Both sides must measure the same thing or every entry is a false miss."""
    # Arrange
    manifest = parse_remote_manifest(_run(tree).stdout)
    # Act
    local = digest_file(os.path.join(str(tree), "a.txt"))
    # Assert
    assert manifest.digests["a.txt"] == local


def test_a_remote_manifest_verifies_against_the_local_walk(tree):
    """End to end: the two producers agree on a whole tree."""
    # Arrange
    remote = parse_remote_manifest(_run(tree).stdout)
    # Act
    verdict = verify_content(digest_tree(str(tree)), remote)
    # Assert
    assert verdict.verdict == VERIFIED


# --------------------------------------------------------------------------
# symlinks -- the two implementations must agree on the convention
# --------------------------------------------------------------------------
def test_a_symlink_digests_its_target_string_on_both_sides(tmp_path):
    # Arrange
    root = tmp_path / "s"
    root.mkdir()
    os.symlink("some/target", os.path.join(str(root), "link"))
    # Act
    manifest = parse_remote_manifest(_run(root).stdout)
    # Assert
    assert manifest.digests["link"] == local_symlink_digest("some/target")


def test_a_symlink_agrees_with_digest_file(tmp_path):
    """Guards the contract between the shell string and the Python one."""
    # Arrange
    root = tmp_path / "s"
    root.mkdir()
    link = os.path.join(str(root), "link")
    os.symlink("some/target", link)
    # Act
    manifest = parse_remote_manifest(_run(root).stdout)
    # Assert
    assert manifest.digests["link"] == digest_file(link)


# --------------------------------------------------------------------------
# the refusals -- an empty answer is never an empty tree
# --------------------------------------------------------------------------
def test_a_missing_root_emits_the_marker(tmp_path):
    # Arrange
    absent = tmp_path / "not-there"
    # Act
    out = _run(absent).stdout
    # Assert
    assert MISSING_ROOT_MARKER in out


def test_a_missing_root_parses_as_root_missing(tmp_path):
    # Arrange
    absent = tmp_path / "not-there"
    # Act
    manifest = parse_remote_manifest(_run(absent).stdout)
    # Assert
    assert manifest.root_missing is True


def test_empty_output_is_not_an_empty_tree():
    """The single most important assertion in this file.

    A connected-but-silent probe and a genuinely empty directory are the same
    zero bytes, and only one of them is a measurement.
    """
    # Arrange
    silence = ""
    # Act
    manifest = parse_remote_manifest(silence)
    # Assert
    assert manifest.unreadable != {}


def test_empty_output_yields_could_not_look_not_verified():
    # Arrange
    remote = parse_remote_manifest("")
    local = digest_tree(os.path.dirname(os.path.abspath(__file__)))
    # Act
    verdict = verify_content(local, remote)
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_a_failed_probe_is_recorded_even_when_stdout_looks_fine():
    """ssh-level failure is a separate fact from the command's output."""
    # Arrange
    plausible = "a" * 64 + " ./x.txt"
    # Act
    manifest = parse_remote_manifest(plausible, probe_succeeded=False)
    # Assert
    assert manifest.digests == {}


def test_an_unreadable_entry_is_kept_as_its_own_population():
    # Arrange
    out = f"{UNREADABLE_MARKER} ./locked.bin"
    # Act
    manifest = parse_remote_manifest(out)
    # Assert
    assert manifest.unreadable == {"locked.bin": "the remote could not hash this entry"}


def test_an_unrecognised_digest_field_is_not_silently_accepted():
    """A shape nobody planned for must poison the verdict, not pass it."""
    # Arrange
    out = "not-a-hash ./x.txt"
    # Act
    manifest = parse_remote_manifest(out)
    # Assert
    assert "x.txt" in manifest.unreadable


def test_a_leading_dot_slash_is_stripped_so_paths_match_the_local_walk():
    # Arrange
    out = "b" * 64 + " ./sub/f.txt"
    # Act
    manifest = parse_remote_manifest(out)
    # Assert
    assert "sub/f.txt" in manifest.digests


# --------------------------------------------------------------------------
# filenames with spaces -- the reason we cut columns instead of splitting
# --------------------------------------------------------------------------
def test_a_filename_with_spaces_survives_the_round_trip(tmp_path):
    """`awk '{print $1}'`-style splitting would mangle this into a wrong path."""
    # Arrange
    root = tmp_path / "sp"
    _write(root, "a file with spaces.txt", b"payload")
    # Act
    manifest = parse_remote_manifest(_run(root).stdout)
    # Assert
    assert "a file with spaces.txt" in manifest.digests


def test_a_filename_with_spaces_gets_the_right_digest(tmp_path):
    # Arrange
    root = tmp_path / "sp"
    _write(root, "a file with spaces.txt", b"payload")
    # Act
    manifest = parse_remote_manifest(_run(root).stdout)
    # Assert
    assert manifest.digests["a file with spaces.txt"] == digest_file(
        os.path.join(str(root), "a file with spaces.txt")
    )


# --------------------------------------------------------------------------
# it still catches the thing it exists for
# --------------------------------------------------------------------------
def test_same_length_different_content_is_caught_across_the_boundary(tmp_path):
    # Arrange
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src, "f.txt", b"alpha")
    _write(dst, "f.txt", b"ALPHA")
    # Act
    verdict = verify_content(digest_tree(str(src)), parse_remote_manifest(_run(dst).stdout))
    # Assert
    assert verdict.verdict == MISMATCH

# EOF
