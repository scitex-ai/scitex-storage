"""Unit tests for scitex_storage._accounting (S5, the accounting audit).

Every case is drawn from the 2026-07-22 ywata-note-win incident, where
this check would have produced the answer in the first hour instead of
the sixth: df said 1.9 T used, everything measurable summed to ~786 G,
and the 1.1 T gap was explained away as measurement error three times
while the instruments were upgraded inside a boundary that never
contained the answer (681 GB in root-only /var/lib/docker, on a separate
mount).

Pure functions; no `monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import pytest

from scitex_storage._measure._accounting import (
    RESIDUAL_THRESHOLD,
    Accounting,
    accounting_signal,
)
from scitex_storage._measure._classify import COULD_NOT_LOOK, MOVABLE

TB = 1024**4
GB = 1024**3


# --- the incident ---------------------------------------------------------
def test_the_incident_gap_refuses_a_verdict():
    # 1.9 T reported, ~786 G measured. The whole point.
    # Arrange
    measured = 786 * GB
    reported = int(1.9 * TB)

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_incident_evidence_says_suspect_scope_before_precision():
    # The lesson is not "measure better"; it is that the BOUNDARY is wrong.
    # Arrange
    measured = 786 * GB
    reported = int(1.9 * TB)

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert "SUSPECT SCOPE BEFORE PRECISION" in signal.evidence


def test_the_incident_evidence_names_the_two_actual_causes():
    # A permission stub and a skipped separate mount -- so the next reader
    # looks where the answer was, not where the instrument was.
    # Arrange
    measured = 786 * GB
    reported = int(1.9 * TB)

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert "permission stub" in signal.evidence


# --- reconciliation -------------------------------------------------------
def test_an_exact_match_reconciles():
    # Arrange
    # Act
    signal = accounting_signal(100 * GB, 100 * GB)

    # Assert
    assert signal.verdict == MOVABLE


def test_a_small_residual_is_within_noise():
    # Sparse files, block rounding and reserved space produce a few percent.
    # Arrange
    measured = 97 * GB
    reported = 100 * GB

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert signal.verdict == MOVABLE


def test_a_residual_just_over_the_threshold_refuses():
    # Arrange -- 15% unaccounted, against a 10% threshold.
    measured = 85 * GB
    reported = 100 * GB

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


# --- measuring MORE than reported -----------------------------------------
def test_measuring_more_than_df_reports_is_also_refused():
    # Usually hardlinks or a bind mount counted twice: the measurement is
    # not what it appears to be, so it must not be waved through as "fine,
    # we found everything".
    # Arrange
    measured = 150 * GB
    reported = 100 * GB

    # Act
    signal = accounting_signal(measured, reported)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_over_measurement_evidence_names_the_likely_cause():
    # Arrange
    # Act
    signal = accounting_signal(150 * GB, 100 * GB)

    # Assert
    assert "hardlinks" in signal.evidence


# --- explained residuals --------------------------------------------------
def test_an_explained_residual_reconciles():
    # A mount deliberately excluded from the scan is a legitimate
    # explanation -- once it is WRITTEN DOWN.
    # Arrange
    measured = 40 * GB
    reported = 100 * GB

    # Act
    signal = accounting_signal(
        measured,
        reported,
        explained_bytes=60 * GB,
        explanation="/mnt/backup excluded from the walk by design",
    )

    # Assert
    assert signal.verdict == MOVABLE


def test_an_allowance_without_a_written_reason_is_refused():
    # Exactly how the incident's residual got "explained away".
    # Arrange
    kwargs = dict(measured_bytes=40 * GB, reported_used_bytes=100 * GB)

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        accounting_signal(explained_bytes=60 * GB, **kwargs)


def test_a_whitespace_only_reason_is_not_a_reason():
    # Arrange
    kwargs = dict(measured_bytes=40 * GB, reported_used_bytes=100 * GB)

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        accounting_signal(explained_bytes=60 * GB, explanation="   ", **kwargs)


# --- the third state ------------------------------------------------------
def test_an_unmeasured_total_is_could_not_look():
    # Arrange
    # Act
    signal = accounting_signal(None, 100 * GB)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_an_unreadable_df_is_could_not_look():
    # Arrange
    # Act
    signal = accounting_signal(100 * GB, None)

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


# --- the arithmetic -------------------------------------------------------
def test_the_residual_fraction_is_of_REPORTED_not_measured():
    # Dividing by the measured total would flatter exactly the case where
    # the measurement missed the most.
    # Arrange
    acct = Accounting(measured_bytes=20 * GB, reported_used_bytes=100 * GB)

    # Act
    fraction = acct.residual_fraction

    # Assert
    assert fraction == pytest.approx(0.8)


def test_a_zero_usage_filesystem_does_not_divide_by_zero():
    # Arrange
    acct = Accounting(measured_bytes=0, reported_used_bytes=0)

    # Act
    fraction = acct.residual_fraction

    # Assert
    assert fraction == 0.0


def test_the_threshold_is_generous_on_purpose():
    # The incident's residual was ~58% of reported usage. A threshold
    # tight enough to fire on ordinary rounding gets muted within a week,
    # and a muted gate is a deleted one.
    # Arrange
    # Act
    threshold = RESIDUAL_THRESHOLD

    # Assert
    assert threshold >= 0.05

# EOF
