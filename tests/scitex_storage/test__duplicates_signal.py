"""Unit tests for S7 -- duplicate detection as a classifier signal.

Card movability-classifier-deterministic-signals-20260723 calls duplicates
"the only class that frees space at zero risk and zero loss -- nothing
moves, nothing is lost, no owner needs consulting. Always run first."

`find_duplicates` has existed since before that card; what was missing was
exposing it as a Signal so `classify()` can use it. These tests pin the two
things that are easy to get backwards:

* the verdict DIRECTION (finding duplicates does not make a tree movable,
  and finding none does not block it), and
* the difference between "scan found nothing" and "scan did not run".

Pure functions over real tmp_path files; no `monkeypatch`, which this repo
bans, and no fclones invocation -- the group structure is the input.
"""

from __future__ import annotations

from pathlib import Path

from scitex_storage._classify import COULD_NOT_LOOK, MOVABLE, NOT_MOVABLE
from scitex_storage._duplicates import duplicates_signal, reclaimable_bytes


def _group(tmp_path: Path, name: str, copies: int, size: int) -> list[Path]:
    paths = []
    for i in range(copies):
        p = tmp_path / f"{name}-{i}.bin"
        p.write_bytes(b"\x7f" * size)
        paths.append(p)
    return paths


# --- reclaimable bytes ----------------------------------------------------
def test_one_copy_of_each_group_is_never_counted(tmp_path):
    # Deleting EVERY copy is data loss, not reclamation.
    # Arrange -- 3 copies of a 100-byte file: 2 are redundant.
    groups = [_group(tmp_path, "a", 3, 100)]

    # Act
    recoverable = reclaimable_bytes(groups)

    # Assert
    assert recoverable == 200


def test_multiple_groups_sum(tmp_path):
    # Arrange
    groups = [_group(tmp_path, "a", 2, 50), _group(tmp_path, "b", 4, 10)]

    # Act
    recoverable = reclaimable_bytes(groups)

    # Assert
    assert recoverable == 50 + 30


def test_zero_byte_duplicates_recover_zero_which_is_a_measurement(tmp_path):
    # Real case: empty files duplicate freely and recover nothing. That is
    # an answer, not a failure to answer.
    # Arrange
    groups = [_group(tmp_path, "empty", 5, 0)]

    # Act
    recoverable = reclaimable_bytes(groups)

    # Assert
    assert recoverable == 0


def test_wholly_unmeasurable_groups_are_none_not_zero(tmp_path):
    # Arrange -- paths that do not exist cannot be sized.
    groups = [[tmp_path / "gone-0.bin", tmp_path / "gone-1.bin"]]

    # Act
    recoverable = reclaimable_bytes(groups)

    # Assert
    assert recoverable is None


def test_a_group_falls_back_to_a_readable_member(tmp_path):
    # fclones already proved the group byte-identical, so any member's size
    # is the group's size -- one unreadable member must not lose the group.
    # Arrange
    real = _group(tmp_path, "a", 2, 80)
    groups = [[tmp_path / "missing.bin"] + real]

    # Act
    recoverable = reclaimable_bytes(groups)

    # Assert
    assert recoverable == 160


# --- the signal's verdict direction ---------------------------------------
def test_finding_duplicates_does_not_make_a_tree_NOT_movable(tmp_path):
    # The subtle one. A duplicate is not a HOLDER -- treating it as an
    # obstacle would block the safest reclaim there is.
    # Arrange
    groups = [_group(tmp_path, "a", 3, 100)]

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert signal.verdict != NOT_MOVABLE


def test_a_completed_scan_with_duplicates_is_movable(tmp_path):
    # Arrange
    groups = [_group(tmp_path, "a", 3, 100)]

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert signal.verdict == MOVABLE


def test_a_completed_scan_with_no_duplicates_is_also_movable(tmp_path):
    # Finding none must not block anything either.
    # Arrange
    groups: list[list[Path]] = []

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert signal.verdict == MOVABLE


def test_the_evidence_names_the_recoverable_total(tmp_path):
    # The number a human actually acts on.
    # Arrange
    groups = [_group(tmp_path, "a", 3, 100)]

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert "200 bytes recoverable" in signal.evidence


def test_the_evidence_says_to_run_it_first(tmp_path):
    # Arrange
    groups = [_group(tmp_path, "a", 2, 10)]

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert "before any move" in signal.evidence


# --- the third state ------------------------------------------------------
def test_an_unrun_scan_is_could_not_look_not_no_duplicates():
    # An unrun scan and a clean tree both produce an empty answer, and only
    # one of them is evidence.
    # Arrange
    # Act
    signal = duplicates_signal(None)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_unrun_evidence_distinguishes_it_from_a_clean_tree():
    # Arrange
    # Act
    signal = duplicates_signal(None)

    # Assert
    assert "only one of them is evidence" in signal.evidence


def test_groups_found_but_none_sizeable_is_could_not_look(tmp_path):
    # A reclaim that cannot be quantified must not be reported as a number.
    # Arrange
    groups = [[tmp_path / "gone-0.bin", tmp_path / "gone-1.bin"]]

    # Act
    signal = duplicates_signal(groups)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK

# EOF
