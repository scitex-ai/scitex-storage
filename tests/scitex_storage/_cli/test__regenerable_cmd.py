"""The `regenerable` verb: the detector reachable across a process boundary.

paper-scitex-clew's consumer is a bash function sourced by a cohort
launcher inside a solver container on a Spartan compute node. They
measured what I had not: `scitex_storage` is not importable there, and no
CLI verb existed. So the detector was built, tested, merged and DESCRIBED
AS USABLE while being unreachable from the process that needs it.

These tests pin the two things a shell caller actually depends on: the
EXIT CODE and the JSON SHAPE. Both are contracts, and both are asserted
here rather than left to prose.

Real tmp_path trees and click's CliRunner; no `monkeypatch`, which this
repo bans.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from click.testing import CliRunner

from scitex_storage._cli._regenerable_cmd import regenerable_cmd

WIRE_KEYS = {
    "path",
    "verdict",
    "ecosystem",
    "marker",
    "spec_path",
    "spec_provenance",
    "reason",
    "evidence",
}


def _venv(root: Path, name: str = "env") -> Path:
    env = root / name
    env.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return env


# --- the examples a consumer copies must actually run ---------------------
def test_every_help_example_names_the_verb_as_its_own_argument():
    # 0.3.0 shipped `find-recipe/path/to/capsule/pylibs` and
    # `find-recipeENV`: both un-runnable if copy-pasted. The
    # `regenerable` -> `find-recipe` rename was applied as a substitution
    # that ate the trailing space, and the rename WAS verified -- against
    # the JSON keys and the exit codes, the half a machine reads. Nothing
    # looked at the half a human starts from. Asserted by parsing the
    # rendered help the way a shell would, so prose cannot drift from it.
    # Arrange
    help_text = CliRunner().invoke(regenerable_cmd, ["--help"]).output
    example_lines = [
        line.strip()[2:]
        for line in help_text.splitlines()
        if line.strip().startswith("$ ")
    ]

    # Act
    verbs_present = [
        "find-recipe" in shlex.split(line)[:3] for line in example_lines
    ]

    # Assert
    assert example_lines and all(verbs_present)


# --- exit codes are the contract for a shell caller -----------------------
def test_regenerable_exits_zero(tmp_path):
    # Arrange
    env = _venv(tmp_path)
    (tmp_path / "requirements.txt").write_text("numpy\n")

    # Act
    result = CliRunner().invoke(
        regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"]
    )

    # Assert
    assert result.exit_code == 0


def test_a_cache_also_exits_zero(tmp_path):
    # Same action is licensed, so the same code. The distinction lives in
    # the JSON, not in two exit codes saying the same thing.
    # Arrange
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / "CACHEDIR.TAG").write_text("Signature: x\n")

    # Act
    result = CliRunner().invoke(regenerable_cmd, [str(cache), "--json"])

    # Assert
    assert result.exit_code == 0


def test_not_regenerable_exits_10_not_1(tmp_path):
    # 1 is "generic failure" in every CLI framework. A renamed or missing
    # verb must not be able to impersonate a real verdict.
    # Arrange
    env = _venv(tmp_path)

    # Act
    result = CliRunner().invoke(
        regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"]
    )

    # Assert
    assert result.exit_code == 10


def test_could_not_look_exits_11_not_2(tmp_path):
    # 2 is "usage error". Same reasoning.
    # Arrange
    missing = tmp_path / "gone"

    # Act
    result = CliRunner().invoke(regenerable_cmd, [str(missing), "--json"])

    # Assert
    assert result.exit_code == 11


def test_a_caller_checking_only_nonzero_gets_the_safe_reading(tmp_path):
    # The common shell idiom is `if cmd; then`. Anything that is not a
    # positive verdict must be non-zero so that idiom fails safe.
    # Arrange
    env = _venv(tmp_path)  # no spec -> not-regenerable

    # Act
    result = CliRunner().invoke(
        regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"]
    )

    # Assert
    assert result.exit_code != 0


# --- the JSON shape is the other contract ---------------------------------
def test_every_wire_key_is_present_on_a_positive_verdict(tmp_path):
    # A caller must never have to ask whether a key exists.
    # Arrange
    env = _venv(tmp_path)
    (tmp_path / "requirements.txt").write_text("numpy\n")

    # Act
    result = CliRunner().invoke(
        regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"]
    )

    # Assert
    assert set(json.loads(result.output)) == WIRE_KEYS


def test_every_wire_key_is_present_on_could_not_look(tmp_path):
    # Including the states where most fields are null -- that is exactly
    # when a missing key would bite.
    # Arrange
    missing = tmp_path / "gone"

    # Act
    result = CliRunner().invoke(regenerable_cmd, [str(missing), "--json"])

    # Assert
    assert set(json.loads(result.output)) == WIRE_KEYS


def test_the_reason_field_reaches_the_wire(tmp_path):
    # A sweep must be able to tell "skip this file" from "your inventory
    # is stale" WITHOUT parsing prose.
    # Arrange
    a_file = tmp_path / "x.tar.gz"
    a_file.write_bytes(b"\x1f\x8b")

    # Act
    result = CliRunner().invoke(regenerable_cmd, [str(a_file), "--json"])

    # Assert
    assert json.loads(result.output)["reason"] == "not-a-directory"


def test_absent_and_file_are_distinguishable_on_the_wire(tmp_path):
    # Arrange
    missing = tmp_path / "gone"

    # Act
    result = CliRunner().invoke(regenerable_cmd, [str(missing), "--json"])

    # Assert
    assert json.loads(result.output)["reason"] == "absent"


# --- --spec crosses the boundary intact -----------------------------------
def test_a_supplied_spec_makes_it_regenerable(tmp_path):
    # The capsule case: the recipe is in a sibling tree no walk can reach.
    # Arrange
    env = _venv(tmp_path / "runs")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    dockerfile = corpus / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    # Act
    result = CliRunner().invoke(
        regenerable_cmd,
        [str(env), "--stop-at", str(tmp_path / "runs"), "--spec", str(dockerfile), "--json"],
    )

    # Assert
    assert result.exit_code == 0


def test_the_provenance_field_says_supplied(tmp_path):
    # A machine consumer cannot read prose, so this is its own field.
    # Arrange
    env = _venv(tmp_path / "runs")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    dockerfile = corpus / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    # Act
    result = CliRunner().invoke(
        regenerable_cmd,
        [str(env), "--stop-at", str(tmp_path / "runs"), "--spec", str(dockerfile), "--json"],
    )

    # Assert
    assert json.loads(result.output)["spec_provenance"] == "supplied"


def test_the_provenance_field_says_discovered_when_found(tmp_path):
    # Arrange
    env = _venv(tmp_path)
    (tmp_path / "requirements.txt").write_text("numpy\n")

    # Act
    result = CliRunner().invoke(
        regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"]
    )

    # Assert
    assert json.loads(result.output)["spec_provenance"] == "discovered"


def test_a_named_spec_that_does_not_exist_still_refuses(tmp_path):
    # Naming a path is not evidence it is there. This is the whole reason
    # the cohort-A clearance was withdrawn once already.
    # Arrange
    env = _venv(tmp_path / "runs")
    ghost = tmp_path / "nowhere" / "Dockerfile"

    # Act
    result = CliRunner().invoke(
        regenerable_cmd,
        [str(env), "--stop-at", str(tmp_path / "runs"), "--spec", str(ghost), "--json"],
    )

    # Assert
    assert result.exit_code == 10


def test_repeated_spec_flags_are_all_considered(tmp_path):
    # A caller may offer the Dockerfile and the tarball and let the
    # detector take whichever actually exists.
    # Arrange
    env = _venv(tmp_path / "runs")
    real = tmp_path / "corpus" / "Dockerfile"
    real.parent.mkdir(parents=True)
    real.write_text("FROM python:3.11\n")
    stale = tmp_path / "old" / "Dockerfile"

    # Act
    result = CliRunner().invoke(
        regenerable_cmd,
        [
            str(env), "--stop-at", str(tmp_path / "runs"),
            "--spec", str(stale), "--spec", str(real), "--json",
        ],
    )

    # Assert
    assert json.loads(result.output)["spec_path"] == str(real)


# --- the verb never acts --------------------------------------------------
def test_the_verb_leaves_the_tree_untouched(tmp_path):
    # It answers one question and returns. Anything that ACTS stays
    # outside, which is the layer split this package is built around.
    # Arrange
    env = _venv(tmp_path)
    (env / "payload.bin").write_bytes(b"\x00" * 16)

    # Act
    CliRunner().invoke(regenerable_cmd, [str(env), "--stop-at", str(tmp_path), "--json"])

    # Assert
    assert (env / "payload.bin").exists()

# EOF
