"""The `survey` verb: the movability classifier reachable across a boundary.

Layer 1's eight signals were built, tested and merged; classify() combined
them; survey() composed them. All of it Python API — and a Python API is
not a capability for a shell script on a compute node or for a GUI that
must render a row per tree. `find-recipe` exists because four PRs of
detector shipped that no consumer could invoke; shipping the composition
layer without a verb would have repeated that knowingly.

These pin what a shell caller actually depends on: the EXIT CODE and the
JSON SHAPE. Both are contracts, both asserted rather than described.

Real tmp_path trees and click's CliRunner; no `monkeypatch`, which this
repo bans.
"""

from __future__ import annotations

import json
import shlex

from click.testing import CliRunner

from scitex_storage._cli._survey_cmd import EXIT_CODES, survey_cmd

WIRE_KEYS = {"path", "verdict", "signals", "reason"}
SIGNAL_KEYS = {"name", "verdict", "evidence"}


def _tree(root, name="t", files=2):
    d = root / name
    d.mkdir(parents=True)
    for i in range(files):
        (d / f"f{i}.txt").write_text(f"x{i}\n")
    return d


# --- exit codes are the contract ----------------------------------------
def test_the_negative_codes_avoid_1_and_2(tmp_path):
    # 1 and 2 already mean "generic failure" and "usage error" in every CLI
    # framework, so a MISSING or renamed verb exits 2 and would impersonate
    # a verdict. That fired for real on 2026-07-28: find-recipe was not
    # installed, exited 2, and had 2 carried a domain meaning the consumer
    # would have read a plausible answer from a command that does not exist.
    # Arrange
    reserved = {1, 2}

    # Act
    used = set(EXIT_CODES.values())

    # Assert
    assert not (used & reserved)


def test_the_codes_do_not_collide_with_find_recipe(tmp_path):
    # A caller shelling BOTH verbs must not have one number mean two
    # things. find-recipe owns 10/11; this owns 12/13.
    # Arrange
    from scitex_storage._cli._regenerable_cmd import EXIT_CODES as RECIPE_CODES

    # Act
    overlap = (set(EXIT_CODES.values()) - {0}) & (set(RECIPE_CODES.values()) - {0})

    # Assert
    assert not overlap


def test_an_absent_path_exits_could_not_look(tmp_path):
    # Not a verdict about the tree — the ABSENCE of one.
    # Arrange
    missing = tmp_path / "gone"

    # Act
    result = CliRunner().invoke(survey_cmd, [str(missing), "--json"])

    # Assert
    assert result.exit_code == 13


def test_an_unreadable_subtree_exits_could_not_look(tmp_path):
    # The map is incomplete, so the verdict must refuse rather than answer
    # confidently about the readable fraction.
    # Arrange
    import os

    d = _tree(tmp_path)
    locked = d / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)

    # Act
    result = CliRunner().invoke(survey_cmd, [str(d), "--json"])
    os.chmod(locked, 0o755)

    # Assert
    assert result.exit_code == 13


# --- the wire shape is fixed --------------------------------------------
def test_every_wire_key_is_present(tmp_path):
    # A consumer must never ask whether a key exists.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = CliRunner().invoke(survey_cmd, [str(d), "--json"])

    # Assert
    assert set(json.loads(result.output)) == WIRE_KEYS


def test_signals_are_objects_not_flattened_prose(tmp_path):
    # The GUI renders each signal beside its own evidence. A consumer must
    # not have to parse English to find out which probe said what.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = CliRunner().invoke(survey_cmd, [str(d), "--json"])
    signals = json.loads(result.output)["signals"]

    # Assert
    assert all(set(s) == SIGNAL_KEYS for s in signals)


def test_each_signal_carries_its_own_verdict(tmp_path):
    # So a caller can see a tree refused by ONE signal while others were
    # content. The disagreement is the information; summarising buries it.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = CliRunner().invoke(survey_cmd, [str(d), "--json"])
    signals = json.loads(result.output)["signals"]

    # Assert
    assert all(s["verdict"] for s in signals)


def test_the_text_output_prints_every_signal_not_only_the_deciding_ones(tmp_path):
    # Five reclaim candidates were correctly withdrawn on 2026-07-22
    # because someone READ THE EVIDENCE. A display showing only the
    # conclusion optimises for the wrong thing.
    # Arrange
    d = _tree(tmp_path)

    # Act
    result = CliRunner().invoke(survey_cmd, [str(d)])

    # Assert
    assert "coverage" in result.output and "coldness" in result.output


def test_every_help_example_names_the_verb_as_its_own_argument():
    # 0.3.0 shipped `find-recipe/path/...` because a rename ate a trailing
    # space, and the examples were never parsed the way a shell would.
    # Arrange
    help_text = CliRunner().invoke(survey_cmd, ["--help"]).output
    lines = [
        ln.strip()[2:] for ln in help_text.splitlines() if ln.strip().startswith("$ ")
    ]

    # Act
    ok = [bool(lines)] + ["survey" in shlex.split(ln)[:3] for ln in lines]

    # Assert
    assert all(ok)

# EOF
