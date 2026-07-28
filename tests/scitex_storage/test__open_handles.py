"""Unit tests for scitex_storage._open_handles (S2, open-handle check).

The failure this signal exists to prevent: on 2026-07-22, four of five
"dead agent" overlays on ywata-note-win turned out to belong to LIVE
agents. "Regenerable" was true of every one of them and would have
licensed deleting all five.

The failure the POSITIVE CONTROL exists to prevent is subtler: a /proc
scan that cannot run returns an empty set, which is indistinguishable
from "nothing is using this" -- and that is the answer the caller wanted.

The control tests below hold a REAL file open and require the real scan
to find it, so they exercise the mechanism rather than a stand-in. No
`monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import os

import pytest

from scitex_storage._classify import COULD_NOT_LOOK, MOVABLE, NOT_MOVABLE
from scitex_storage._open_handles import (
    holders_under,
    iter_open_paths,
    open_handle_signal,
)


# --- prefix matching ------------------------------------------------------
def test_a_file_inside_the_tree_is_a_holder():
    # Arrange
    observed = ["/data/project/run.h5"]

    # Act
    holders = holders_under("/data/project", observed)

    # Assert
    assert holders == ["/data/project/run.h5"]


def test_a_sibling_with_a_shared_PREFIX_is_not_a_holder():
    # /data/foo must not match /data/foobar -- a plain startswith would
    # block a movable tree on an unrelated neighbour.
    # Arrange
    observed = ["/data/foobar/run.h5"]

    # Act
    holders = holders_under("/data/foo", observed)

    # Assert
    assert holders == []


def test_the_tree_itself_being_open_counts():
    # Arrange
    observed = ["/data/project"]

    # Act
    holders = holders_under("/data/project", observed)

    # Assert
    assert holders == ["/data/project"]


def test_unrelated_paths_are_not_holders():
    # Arrange
    observed = ["/usr/lib/libc.so", "/home/other/x"]

    # Act
    holders = holders_under("/data/project", observed)

    # Assert
    assert holders == []


# --- the positive control -------------------------------------------------
def test_a_blind_scan_is_could_not_look_not_movable(tmp_path):
    # THE central case: an empty result set must never read as "unused".
    # Arrange -- a control the (empty) scan cannot possibly find.
    control = tmp_path / "held.txt"
    control.write_text("x")

    # Act
    signal = open_handle_signal(str(tmp_path), str(control), open_paths=[])

    # Assert
    assert signal.verdict == COULD_NOT_LOOK


def test_the_blind_scan_evidence_names_the_control_failure(tmp_path):
    # Arrange
    control = tmp_path / "held.txt"
    control.write_text("x")

    # Act
    signal = open_handle_signal(str(tmp_path), str(control), open_paths=[])

    # Assert
    assert "POSITIVE CONTROL FAILED" in signal.evidence


def test_a_holder_is_reported_even_when_the_control_passes(tmp_path):
    # Arrange
    control = tmp_path / "held.txt"
    control.write_text("x")
    target = tmp_path / "tree"
    target.mkdir()
    observed = [str(control), str(target / "busy.h5")]

    # Act
    signal = open_handle_signal(str(target), str(control), open_paths=observed)

    # Assert
    assert signal.verdict == NOT_MOVABLE


def test_no_holder_with_a_passing_control_is_movable(tmp_path):
    # Arrange
    control = tmp_path / "held.txt"
    control.write_text("x")
    target = tmp_path / "tree"
    target.mkdir()

    # Act
    signal = open_handle_signal(str(target), str(control), open_paths=[str(control)])

    # Assert
    assert signal.verdict == MOVABLE


def test_the_movable_evidence_states_the_coverage_limit(tmp_path):
    # A control validates the MECHANISM and is silent about COVERAGE.
    # Arrange
    control = tmp_path / "held.txt"
    control.write_text("x")
    target = tmp_path / "tree"
    target.mkdir()

    # Act
    signal = open_handle_signal(str(target), str(control), open_paths=[str(control)])

    # Assert
    assert "COVERAGE CAVEAT" in signal.evidence


# --- the REAL /proc scan --------------------------------------------------
@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no procfs")
def test_the_real_scan_finds_a_file_this_process_holds_open(tmp_path):
    # Exercises the actual mechanism, not a stand-in: if this fails, the
    # probe is blind on this platform and every verdict built on it is
    # worthless -- which is precisely what the control is for.
    # Arrange
    held = tmp_path / "really-open.bin"
    held.write_bytes(b"\0")

    # Act
    with open(held, "rb"):
        observed = list(iter_open_paths())

    # Assert
    assert os.path.realpath(str(held)) in observed


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no procfs")
def test_the_real_scan_backs_a_not_movable_verdict(tmp_path):
    # Arrange
    target = tmp_path / "tree"
    target.mkdir()
    busy = target / "busy.bin"
    busy.write_bytes(b"\0")

    # Act -- the `with` block is what holds the file open; the probe runs
    # inside it and the handle needs no assertion of its own.
    with open(busy, "rb"):
        signal = open_handle_signal(str(target), str(busy))

    # Assert
    assert signal.verdict == NOT_MOVABLE


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no procfs")
def test_the_real_scan_reports_movable_for_an_untouched_tree(tmp_path):
    # Arrange -- the control lives OUTSIDE the tree being judged.
    control = tmp_path / "control.bin"
    control.write_bytes(b"\0")
    target = tmp_path / "quiet"
    target.mkdir()
    (target / "cold.bin").write_bytes(b"\0")

    # Act
    with open(control, "rb"):
        signal = open_handle_signal(str(target), str(control))

    # Assert
    assert signal.verdict == MOVABLE

# EOF
