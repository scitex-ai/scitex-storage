"""Unit tests for scitex_storage._classify (Layer 1, MECHANICAL).

Every case here is a real failure from the 2026-07-22 ywata-note-win
incident, encoded so it cannot recur silently:

* an actively-READ corpus that an mtime-only probe called cold
* a destination that was a directory named like a mount
* a cleanup verb with no destination free-space probe
* N timestamps that were one event
* a permission stub reported as a size

The functions are pure (or take a real tmp_path), so none of this needs
`monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import os

import pytest

from scitex_storage._classify import (
    COULD_NOT_LOOK,
    MOVABLE,
    NOT_MOVABLE,
    Signal,
    classify,
    clustering_signal,
    coldness_signal,
    combine,
    destination_signal,
    free_space_signal,
    readability_signal,
)

DAY = 86400.0
NOW = 1_000_000_000.0


# --- combine ---------------------------------------------------------------
def test_no_signals_is_not_a_clean_bill_of_health():
    # Arrange
    # Act
    verdict = combine([])

    # Assert
    assert verdict == COULD_NOT_LOOK


def test_a_single_could_not_look_poisons_the_verdict():
    # Arrange
    signals = [
        Signal("a", MOVABLE, "fine"),
        Signal("b", COULD_NOT_LOOK, "probe did not run"),
    ]

    # Act
    verdict = combine(signals)

    # Assert
    assert verdict == COULD_NOT_LOOK


def test_one_holder_outranks_any_number_of_agreements():
    # Arrange
    signals = [
        Signal("a", MOVABLE, "fine"),
        Signal("b", MOVABLE, "fine"),
        Signal("c", NOT_MOVABLE, "something is standing on it"),
    ]

    # Act
    verdict = combine(signals)

    # Assert
    assert verdict == NOT_MOVABLE


def test_unanimous_agreement_is_movable():
    # Arrange
    signals = [Signal("a", MOVABLE, "fine"), Signal("b", MOVABLE, "fine")]

    # Act
    verdict = combine(signals)

    # Assert
    assert verdict == MOVABLE


def test_a_verdict_without_evidence_is_refused():
    # Arrange -- a verdict that cannot be audited is not a measurement.
    blank_evidence = "   "

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        Signal("a", MOVABLE, blank_evidence)


def test_an_unknown_verdict_is_refused():
    # Arrange
    bogus_verdict = "probably-fine"

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        Signal("a", bogus_verdict, "hand-wave")


def test_classification_reports_the_deciding_signal():
    # Arrange
    signals = [
        Signal("coldness", MOVABLE, "cold"),
        Signal("destination", NOT_MOVABLE, "same filesystem"),
    ]

    # Act
    result = classify("/tmp/x", signals)

    # Assert
    assert "same filesystem" in result.reason


# --- S1 coldness -----------------------------------------------------------
def test_a_recent_read_blocks_the_move_even_when_writes_are_ancient():
    # The ai-for-science case: 15 days without a write, READ 11h ago.
    # Arrange
    # Act
    signal = coldness_signal(
        newest_mtime=NOW - 15 * DAY,
        newest_atime=NOW - 0.5 * DAY,
        now=NOW,
        cold_after_seconds=7 * DAY,
    )

    # Assert
    assert signal.verdict == NOT_MOVABLE


def test_the_recent_read_evidence_names_the_reader_problem():
    # Arrange
    # Act
    signal = coldness_signal(NOW - 15 * DAY, NOW - 0.5 * DAY, NOW, 7 * DAY)

    # Assert
    assert "a reader leaves no mtime" in signal.evidence


def test_old_writes_and_old_reads_are_movable():
    # Arrange
    # Act
    signal = coldness_signal(NOW - 300 * DAY, NOW - 300 * DAY, NOW, 7 * DAY)

    # Assert
    assert signal.verdict == MOVABLE


def test_a_missing_atime_is_not_treated_as_old():
    # Arrange
    # Act
    signal = coldness_signal(NOW - 300 * DAY, None, NOW, 7 * DAY)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


# --- S3 destination --------------------------------------------------------
def test_a_destination_absent_from_proc_mounts_is_refused():
    # /mnt/nas2 as a bare directory on the full root filesystem.
    # Arrange
    # Act
    signal = destination_signal(dest_fsid=1, source_fsid=2, dest_in_mounts=False)

    # Assert
    assert signal.verdict == NOT_MOVABLE


def test_a_destination_on_the_source_filesystem_frees_nothing():
    # Arrange
    # Act
    signal = destination_signal(dest_fsid=7, source_fsid=7, dest_in_mounts=True)

    # Assert
    assert signal.verdict == NOT_MOVABLE


def test_a_distinct_mounted_filesystem_is_accepted():
    # Arrange
    # Act
    signal = destination_signal(dest_fsid=7, source_fsid=9, dest_in_mounts=True)

    # Assert
    assert signal.verdict == MOVABLE


# --- S4 free space ---------------------------------------------------------
def test_an_artifact_larger_than_the_destination_is_refused():
    # The sweep bug: a 187 GiB tar onto 2.3 GB of free space.
    # Arrange
    # Act
    signal = free_space_signal(needed_bytes=200_000_000_000, available_bytes=2_300_000_000)

    # Assert
    assert signal.verdict == NOT_MOVABLE


def test_the_shortfall_is_named_rather_than_just_refused():
    # Arrange
    # Act
    signal = free_space_signal(200_000_000_000, 2_300_000_000)

    # Assert
    assert "short by" in signal.evidence


def test_an_unknown_destination_size_is_not_assumed_roomy():
    # Arrange
    # Act
    signal = free_space_signal(needed_bytes=10, available_bytes=None)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_sufficient_space_is_accepted():
    # Arrange
    # Act
    signal = free_space_signal(1_000_000_000, 500_000_000_000)

    # Assert
    assert signal.verdict == MOVABLE


# --- S6 clustering ---------------------------------------------------------
def test_timestamps_inside_one_minute_are_one_event_not_many_facts():
    # 37 overlays "written 0.3d ago" -- a single hook push.
    # Arrange
    # Act
    signal = clustering_signal([NOW, NOW + 1, NOW + 2, NOW + 3])

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_genuinely_spread_timestamps_are_usable():
    # Arrange
    # Act
    signal = clustering_signal([NOW, NOW + 5 * DAY, NOW + 40 * DAY])

    # Assert
    assert signal.verdict == MOVABLE


def test_too_few_timestamps_to_cluster_is_not_an_obstacle():
    # Arrange
    # Act
    signal = clustering_signal([NOW, NOW + 1])

    # Assert
    assert signal.verdict == MOVABLE


# --- S8 readability --------------------------------------------------------
def test_a_readable_directory_is_reported_readable(tmp_path):
    # Arrange
    # Act
    signal = readability_signal(str(tmp_path))

    # Assert
    assert signal.verdict == MOVABLE


def test_a_missing_path_is_could_not_look(tmp_path):
    # Arrange
    # Act
    signal = readability_signal(str(tmp_path / "does-not-exist"))

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_an_unreadable_directory_is_could_not_look_rather_than_empty(tmp_path):
    # The /var/lib/docker case: 681 GB reported as a 4.0K stub.
    # Arrange
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)

    # Act
    signal = readability_signal(str(locked))
    locked.chmod(0o755)  # so pytest can clean up

    # Assert
    assert signal.verdict == COULD_NOT_LOOK

# EOF
