#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: tests/scitex_storage/_cli/test__verify_content_cmd.py
"""Tests for the ``verify-content`` verb.

THE EXIT CODES ARE THE CONTRACT, so they are what these tests assert. A
consumer shelling this verb reads the number, not the prose, and a verb whose
codes drift is worse than one that does not exist -- the caller keeps reading
a plausible answer.

The suite asserts 0 is REACHABLE. A verb that can only ever refuse would pass
every test that checks the refusals, and would be useless in exactly the way
that looks like caution.
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from scitex_storage._cli._verify_content_cmd import EXIT_CODES, verify_content_cmd
from scitex_storage._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED


def _write(root, rel, data):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)


@pytest.fixture
def matching(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for root in (src, dst):
        _write(str(root), "a.txt", b"alpha")
        _write(str(root), "sub/b.bin", b"\x00\x01\x02" * 50)
    return str(src), str(dst)


@pytest.fixture
def corrupted(matching):
    """Same names, same sizes, different bytes."""
    src, dst = matching
    _write(dst, "a.txt", b"ALPHA")
    return src, dst


# --------------------------------------------------------------------------
# the declared codes -- 0 / 14 / 15, and never 1 or 2
# --------------------------------------------------------------------------
def test_matching_trees_exit_zero(matching):
    # Arrange
    src, dst = matching
    # Act
    result = CliRunner().invoke(verify_content_cmd, [src, dst])
    # Assert
    assert result.exit_code == 0


def test_corrupted_destination_exits_fourteen(corrupted):
    # Arrange
    src, dst = corrupted
    # Act
    result = CliRunner().invoke(verify_content_cmd, [src, dst])
    # Assert
    assert result.exit_code == 14


def test_missing_destination_exits_fifteen(tmp_path):
    """could-not-look, NOT mismatch: nothing was read back."""
    # Arrange
    src = tmp_path / "src"
    _write(str(src), "a.txt", b"alpha")
    # Act
    result = CliRunner().invoke(verify_content_cmd, [str(src), str(tmp_path / "nope")])
    # Assert
    assert result.exit_code == 15


def test_a_refusal_never_exits_one_or_two(tmp_path):
    """1 and 2 are reserved: a missing verb exits 2 and must not read as a verdict."""
    # Arrange
    src = tmp_path / "src"
    _write(str(src), "a.txt", b"alpha")
    # Act
    result = CliRunner().invoke(verify_content_cmd, [str(src), str(tmp_path / "nope")])
    # Assert
    assert result.exit_code not in (1, 2)


def test_the_three_verdicts_map_to_distinct_codes():
    """No number may mean two things."""
    # Arrange
    codes = [EXIT_CODES[VERIFIED], EXIT_CODES[MISMATCH], EXIT_CODES[COULD_NOT_LOOK]]
    # Act
    distinct = set(codes)
    # Assert
    assert len(distinct) == 3


def test_the_codes_do_not_collide_with_the_survey_verbs_range():
    """find-recipe owns 10/11, survey owns 12/13; this owns 14/15."""
    # Arrange
    mine = {EXIT_CODES[MISMATCH], EXIT_CODES[COULD_NOT_LOOK]}
    # Act
    overlap = mine & {10, 11, 12, 13, 20}
    # Assert
    assert overlap == set()


# --------------------------------------------------------------------------
# the denominator must be printed even when nothing is wrong
# --------------------------------------------------------------------------
def test_a_passing_run_still_prints_the_entry_count(matching):
    """"0 mismatched" is not a result; "0 of N" is."""
    # Arrange
    src, dst = matching
    # Act
    result = CliRunner().invoke(verify_content_cmd, [src, dst])
    # Assert
    assert "2 observed / 2 expected" in result.output


def test_json_output_carries_may_remove_source(matching):
    # Arrange
    src, dst = matching
    # Act
    result = CliRunner().invoke(verify_content_cmd, [src, dst, "--json"])
    # Assert
    assert json.loads(result.output)["may_remove_source"] is True


def test_json_output_carries_may_remove_source_false_on_corruption(corrupted):
    # Arrange
    src, dst = corrupted
    # Act
    result = CliRunner().invoke(verify_content_cmd, [src, dst, "--json"])
    # Assert
    assert json.loads(result.output)["may_remove_source"] is False


def test_json_keeps_unmeasured_bytes_as_null_rather_than_omitting_them(matching):
    """A missing key and 'not measured' must not be indistinguishable."""
    # Arrange
    src, dst = matching
    # Act
    payload = json.loads(CliRunner().invoke(verify_content_cmd, [src, dst, "--json"]).output)
    # Assert
    assert payload["expected_bytes"] is None


def test_json_shape_is_the_same_on_a_refusal(tmp_path):
    """Every key present on every call -- a caller never guesses."""
    # Arrange
    src = tmp_path / "src"
    _write(str(src), "a.txt", b"alpha")
    # Act
    payload = json.loads(
        CliRunner().invoke(
            verify_content_cmd, [str(src), str(tmp_path / "nope"), "--json"]
        ).output
    )
    # Assert
    assert set(payload) == {
        "source",
        "destination",
        "verdict",
        "may_remove_source",
        "expected_count",
        "observed_count",
        "expected_bytes",
        "observed_bytes",
        "evidence",
    }


def test_the_verb_is_registered_in_the_lazy_registry():
    """An unregistered verb is a library, not a capability."""
    # Arrange
    from scitex_storage._cli._lazy import VERB_REGISTRY
    # Act
    target = VERB_REGISTRY.get("verify-content")
    # Assert
    assert target == "._verify_content_cmd:verify_content_cmd"

# EOF
