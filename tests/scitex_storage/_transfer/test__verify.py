"""Unit tests for scitex_storage._verify.

Each case is a real failure this project has already paid for:

* a baseline drawn from a narrower population than the transport writes
  (to_nas, 2026-07-23: predicted 322,183, got 342,677, all 20,492 symlinks)
* a tolerance that would have swallowed that gap, and would equally swallow
  a truncated transfer
* an unanswered probe read as permission to delete

All pure functions or real tmp_path trees, so nothing here needs
`monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import os

import pytest

from scitex_storage._transfer._verify import (
    COULD_NOT_LOOK,
    MISMATCH,
    VERIFIED,
    RemoteTally,
    TransferVerdict,
    local_tally,
    parse_remote_tally,
    verify_transfer,
)


# --- the comparator -------------------------------------------------------
def test_a_matching_destination_is_verified():
    # Arrange
    observed = RemoteTally(entry_count=100, size_bytes=5000)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == VERIFIED


def test_a_short_destination_is_a_mismatch():
    # Arrange -- 99 of 100 arrived.
    observed = RemoteTally(entry_count=99, size_bytes=4950)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == MISMATCH


def test_a_SURPLUS_is_also_a_mismatch_not_a_pass():
    # The to_nas case: MORE members than predicted, because the baseline
    # counted regular files only. Looking "better" is still unexplained.
    # Arrange
    observed = RemoteTally(entry_count=342_677, size_bytes=10_000)

    # Act
    verdict = verify_transfer(322_183, 10_000, observed)

    # Assert
    assert verdict.verdict == MISMATCH


def test_the_surplus_evidence_names_the_baseline_as_the_suspect():
    # Arrange
    observed = RemoteTally(entry_count=342_677, size_bytes=10_000)

    # Act
    verdict = verify_transfer(322_183, 10_000, observed)

    # Assert
    assert "narrower population" in verdict.evidence


def test_right_count_but_short_bytes_is_caught():
    # Every file present, all truncated -- invisible to a count-only check.
    # Arrange
    observed = RemoteTally(entry_count=100, size_bytes=12)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == MISMATCH


def test_more_bytes_than_the_source_is_not_a_mismatch():
    # Block size and sparseness differ across filesystems; bytes are a
    # lower bound, not an equality.
    # Arrange
    observed = RemoteTally(entry_count=100, size_bytes=9999)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == VERIFIED


# --- the explained shortfall ---------------------------------------------
def test_an_explained_shortfall_is_subtracted_from_the_baseline():
    # 2 sockets tar/rsync cannot represent.
    # Arrange
    observed = RemoteTally(entry_count=98, size_bytes=5000)

    # Act
    verdict = verify_transfer(
        100, 5000, observed, explained_shortfall=2, shortfall_reason="2 sockets"
    )

    # Assert
    assert verdict.verdict == VERIFIED


def test_a_shortfall_allowance_without_a_reason_is_refused():
    # Arrange -- an unexplained allowance is a fudge factor.
    observed = RemoteTally(entry_count=98, size_bytes=5000)

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        verify_transfer(100, 5000, observed, explained_shortfall=2)


def test_a_negative_shortfall_is_refused():
    # Arrange
    observed = RemoteTally(entry_count=100, size_bytes=5000)

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        verify_transfer(100, 5000, observed, explained_shortfall=-1, shortfall_reason="x")


# --- the third state ------------------------------------------------------
def test_an_unanswered_probe_is_could_not_look():
    # Arrange
    observed = RemoteTally(entry_count=None, size_bytes=None, detail="ssh failed")

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_could_not_look_does_not_license_removing_the_source():
    # The whole point: "I could not check" must block the delete exactly as
    # firmly as "the check failed".
    # Arrange
    observed = RemoteTally(entry_count=None, size_bytes=None, detail="ssh failed")

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.may_remove_source is False


def test_a_mismatch_does_not_license_removing_the_source():
    # Arrange
    observed = RemoteTally(entry_count=1, size_bytes=1)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.may_remove_source is False


def test_only_a_verified_result_licenses_removing_the_source():
    # Arrange
    observed = RemoteTally(entry_count=100, size_bytes=5000)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.may_remove_source is True


def test_an_empty_destination_is_a_mismatch_not_a_could_not_look():
    # Zero is a MEASUREMENT ("the destination is empty"), unlike None.
    # Arrange
    observed = RemoteTally(entry_count=0, size_bytes=0)

    # Act
    verdict = verify_transfer(100, 5000, observed)

    # Assert
    assert verdict.verdict == MISMATCH


# --- parsing --------------------------------------------------------------
def test_a_well_formed_tally_parses():
    # Arrange
    stdout = "342677\n10062000000\n"

    # Act
    tally = parse_remote_tally(stdout)

    # Assert
    assert tally.entry_count == 342677


def test_a_truncated_probe_output_is_none_not_zero():
    # A probe degrading to 0 would report an empty destination and send the
    # operator hunting a transfer failure that never happened.
    # Arrange
    stdout = ""

    # Act
    tally = parse_remote_tally(stdout)

    # Assert
    assert tally.entry_count is None


def test_unparseable_numbers_become_none():
    # Arrange
    stdout = "find: permission denied\nalso not a number\n"

    # Act
    tally = parse_remote_tally(stdout)

    # Assert
    assert tally.size_bytes is None


# --- the local baseline ---------------------------------------------------
def test_local_tally_counts_plain_files(tmp_path):
    # Arrange
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")

    # Act
    tally = local_tally(str(tmp_path))

    # Assert
    assert tally.entry_count == 2


def test_local_tally_counts_a_symlink_to_a_FILE(tmp_path):
    # Arrange
    (tmp_path / "real.txt").write_text("x")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")

    # Act
    tally = local_tally(str(tmp_path))

    # Assert
    assert tally.entry_count == 2


def test_local_tally_counts_a_symlink_to_a_DIRECTORY(tmp_path):
    # The to_nas lesson: rsync -a WRITES this as a symlink, so the baseline
    # must count it, even though the inode model deliberately does not.
    # Arrange
    target = tmp_path / "realdir"
    target.mkdir()
    (target / "inner.txt").write_text("x")
    (tmp_path / "dirlink").symlink_to(target)

    # Act
    tally = local_tally(str(tmp_path))

    # Assert
    assert tally.entry_count == 2


def test_local_tally_does_not_descend_through_a_directory_symlink(tmp_path):
    # Descending would double-count inner.txt and inflate the baseline.
    # Arrange
    target = tmp_path / "realdir"
    target.mkdir()
    (target / "inner.txt").write_text("x")
    (tmp_path / "dirlink").symlink_to(target)

    # Act
    tally = local_tally(str(tmp_path))

    # Assert
    assert tally.entry_count == 2


def test_local_tally_of_a_missing_path_is_none_not_zero(tmp_path):
    # os.walk() on a missing dir yields nothing and raises nothing, so the
    # naive implementation reports 0 entries -- a measurement, and the
    # convenient one. This bit for real: the archive read-back reported
    # "destination holds 0 entries" when the path was simply wrong.
    # Arrange
    missing = tmp_path / "nope"

    # Act
    tally = local_tally(str(missing))

    # Assert
    assert tally.entry_count is None


# --- the validator --------------------------------------------------------
def test_a_verdict_without_evidence_is_refused():
    # Arrange
    kwargs = dict(
        verdict=VERIFIED,
        expected_count=1,
        observed_count=1,
        expected_bytes=1,
        observed_bytes=1,
        evidence="  ",
    )

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        TransferVerdict(**kwargs)


def test_an_unknown_verdict_is_refused():
    # Arrange
    kwargs = dict(
        verdict="probably-fine",
        expected_count=1,
        observed_count=1,
        expected_bytes=1,
        observed_bytes=1,
        evidence="hand-wave",
    )

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        TransferVerdict(**kwargs)

# EOF
