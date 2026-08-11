#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: tests/scitex_storage/test__content_verify.py
"""Tests for content-hash verification.

THE SUITE CARRIES POSITIVE CONTROLS ON PURPOSE. A verifier that returns
COULD_NOT_LOOK for everything is perfectly safe and perfectly useless, and it
passes every test that only checks "does it refuse the bad cases". So there
are tests asserting it CAN say VERIFIED, and a discriminating pair near the
bottom asserting it says something the cheap tally check does not.
"""

from __future__ import annotations

import os
import stat

import pytest

from scitex_storage._content_verify import (
    ContentManifest,
    digest_file,
    digest_tree,
    verify_content,
)
from scitex_storage._verify import (
    COULD_NOT_LOOK,
    MISMATCH,
    VERIFIED,
    RemoteTally,
    local_tally,
    verify_transfer,
)


def _write(root, rel, data):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return full


@pytest.fixture
def pair(tmp_path):
    """A source and a destination holding identical content."""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for root in (src, dst):
        _write(str(root), "a.txt", b"alpha")
        _write(str(root), "sub/b.bin", b"\x00\x01\x02" * 100)
    return str(src), str(dst)


@pytest.fixture
def corrupted_pair(pair):
    """Destination differs from source in CONTENT ONLY -- same name, same size.

    This is the exact corruption a count-and-size check cannot see, and it is
    the reason this module exists.
    """
    src, dst = pair
    _write(dst, "a.txt", b"ALPHA")
    return src, dst


# --------------------------------------------------------------------------
# POSITIVE CONTROLS -- it must be able to pass, or every other test is vacuous
# --------------------------------------------------------------------------
def test_identical_trees_are_verified(pair):
    # Arrange
    src, dst = pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == VERIFIED


def test_identical_trees_license_source_removal(pair):
    # Arrange
    src, dst = pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.may_remove_source is True


def test_verified_verdict_reports_the_entry_count_it_compared(pair):
    # Arrange
    src, dst = pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.expected_count == 2


# --------------------------------------------------------------------------
# THE REASON THIS MODULE EXISTS
# --------------------------------------------------------------------------
def test_same_size_different_content_is_a_mismatch(corrupted_pair):
    # Arrange
    src, dst = corrupted_pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == MISMATCH


def test_same_size_different_content_refuses_source_removal(corrupted_pair):
    # Arrange
    src, dst = corrupted_pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.may_remove_source is False


def test_the_tally_check_is_fooled_by_what_content_catches(corrupted_pair):
    """Discriminating control, half one: the cheap check says VERIFIED.

    If this ever fails because the tally ALSO catches it, the corruption being
    simulated is not the class this module was built for, and the companion
    test below is no longer evidence of anything.
    """
    # Arrange
    src, dst = corrupted_pair
    src_tally = local_tally(src)
    # Act
    tally = verify_transfer(
        expected_count=src_tally.entry_count,
        expected_bytes=src_tally.size_bytes,
        observed=local_tally(dst),
    )
    # Assert
    assert tally.may_remove_source is True


def test_the_content_check_is_not_fooled_by_what_the_tally_misses(corrupted_pair):
    """Discriminating control, half two: the strict check says MISMATCH."""
    # Arrange
    src, dst = corrupted_pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.may_remove_source is False


# --------------------------------------------------------------------------
# ordinary mismatches
# --------------------------------------------------------------------------
def test_missing_entry_is_a_mismatch(pair):
    # Arrange
    src, dst = pair
    os.remove(os.path.join(dst, "a.txt"))
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == MISMATCH


def test_missing_entry_is_named_in_the_evidence(pair):
    # Arrange
    src, dst = pair
    os.remove(os.path.join(dst, "a.txt"))
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert "MISSING" in verdict.evidence


def test_surplus_entry_is_a_mismatch(pair):
    """A surplus is disqualifying, not a bonus: it means the baseline is wrong."""
    # Arrange
    src, dst = pair
    _write(dst, "unexpected.txt", b"x")
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == MISMATCH


# --------------------------------------------------------------------------
# the middle state -- the one that is easiest to collapse into a pole
# --------------------------------------------------------------------------
@pytest.fixture
def unreadable_source(pair):
    src, dst = pair
    target = os.path.join(src, "a.txt")
    os.chmod(target, 0)
    if os.access(target, os.R_OK):  # running as root: the mode is advisory
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        pytest.skip("cannot make a file unreadable as this user")
    yield src, dst
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def test_unreadable_entry_is_could_not_look(unreadable_source):
    # Arrange
    src, dst = unreadable_source
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_unreadable_entry_refuses_source_removal(unreadable_source):
    """An unreadable file is NOT a matching file."""
    # Arrange
    src, dst = unreadable_source
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.may_remove_source is False


def test_missing_source_root_is_could_not_look(tmp_path):
    # Arrange
    dst = tmp_path / "dst"
    _write(str(dst), "a.txt", b"alpha")
    # Act
    verdict = verify_content(digest_tree(str(tmp_path / "nope")), digest_tree(str(dst)))
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_missing_source_root_is_not_treated_as_an_empty_source(tmp_path):
    """An unmounted mount point is a readable, error-free, empty directory."""
    # Arrange
    dst = tmp_path / "dst"
    _write(str(dst), "a.txt", b"alpha")
    # Act
    verdict = verify_content(digest_tree(str(tmp_path / "nope")), digest_tree(str(dst)))
    # Assert
    assert verdict.may_remove_source is False


def test_missing_destination_root_is_could_not_look(tmp_path):
    # Arrange
    src = tmp_path / "src"
    _write(str(src), "a.txt", b"alpha")
    # Act
    verdict = verify_content(digest_tree(str(src)), digest_tree(str(tmp_path / "nope")))
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_empty_source_is_not_verified(tmp_path):
    """Zero must not be mistaken for agreement."""
    # Arrange
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    # Act
    verdict = verify_content(digest_tree(str(src)), digest_tree(str(dst)))
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_empty_source_refuses_source_removal(tmp_path):
    # Arrange
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    # Act
    verdict = verify_content(digest_tree(str(src)), digest_tree(str(dst)))
    # Assert
    assert verdict.may_remove_source is False


# --------------------------------------------------------------------------
# symlinks -- digest the target string, never follow
# --------------------------------------------------------------------------
def test_symlink_retarget_is_a_mismatch(pair):
    # Arrange
    src, dst = pair
    os.symlink("a.txt", os.path.join(src, "link"))
    os.symlink("sub/b.bin", os.path.join(dst, "link"))
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == MISMATCH


@pytest.fixture
def dangling_symlink_pair(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for root in (src, dst):
        root.mkdir()
        os.symlink("does/not/exist", os.path.join(str(root), "dangling"))
    return str(src), str(dst)


def test_broken_symlink_is_not_recorded_as_unreadable(dangling_symlink_pair):
    """A dangling symlink is legitimate content, not a read failure."""
    # Arrange
    src, _ = dangling_symlink_pair
    # Act
    manifest = digest_tree(src)
    # Assert
    assert manifest.unreadable == {}


def test_matching_broken_symlinks_verify(dangling_symlink_pair):
    """Following the link would poison the verdict for the whole tree."""
    # Arrange
    src, dst = dangling_symlink_pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.verdict == VERIFIED


def test_symlink_digest_differs_from_the_target_file_digest(tmp_path):
    """Proves we hash the LINK, not what it points at."""
    # Arrange
    root = tmp_path / "r"
    root.mkdir()
    payload = _write(str(root), "real.txt", b"payload")
    link = os.path.join(str(root), "link")
    os.symlink("real.txt", link)
    # Act
    link_digest = digest_file(link)
    # Assert
    assert link_digest != digest_file(payload)


# --------------------------------------------------------------------------
# verdict plumbing
# --------------------------------------------------------------------------
def test_a_partial_walk_does_not_masquerade_as_a_manifest():
    """A manifest built from a failed walk must not compare as content."""
    # Arrange
    broken = ContentManifest(digests={}, unreadable={"<walk>": "OSError: boom"})
    good = ContentManifest(digests={"a": "deadbeef"})
    # Act
    verdict = verify_content(broken, good)
    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_bytes_are_reported_as_none_not_zero(pair):
    """This check does not measure bytes; it must not report a number."""
    # Arrange
    src, dst = pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.expected_bytes is None


def test_observed_bytes_are_reported_as_none_not_zero(pair):
    # Arrange
    src, dst = pair
    # Act
    verdict = verify_content(digest_tree(src), digest_tree(dst))
    # Assert
    assert verdict.observed_bytes is None


def test_could_not_look_is_not_reported_as_a_mismatch():
    """The two refusals have the same consequence and are different facts."""
    # Arrange
    empty_source = ContentManifest(root_missing=True)
    # Act
    verdict = verify_content(empty_source, ContentManifest())
    # Assert
    assert verdict.verdict != MISMATCH


def test_could_not_look_never_licenses_removal():
    # Arrange
    empty_source = ContentManifest(root_missing=True)
    # Act
    verdict = verify_content(empty_source, ContentManifest())
    # Assert
    assert verdict.may_remove_source is False


def test_remote_tally_none_is_not_zero_still_holds():
    """Guards the shared contract this module leans on."""
    # Arrange
    unanswered = RemoteTally(entry_count=None, size_bytes=None)
    # Act
    verdict = verify_transfer(5, 100, unanswered)
    # Assert
    assert verdict.may_remove_source is False

# EOF
