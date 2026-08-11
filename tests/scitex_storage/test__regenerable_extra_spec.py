"""Caller-supplied recipe paths, for recipes an ancestor walk cannot reach.

The real case, measured by paper-scitex-clew on Spartan 2026-07-28:

    env dir      .../runs/cohort_a_scitex/capsule-004/pylibs
    capsule root .../runs/cohort_a_scitex/capsule-004
    the recipe   ~/.scitex/dataset/.../for_solver/capsule-004/input/
                 environment/Dockerfile

The recipe lives in a SIBLING corpus tree, bound to the run directory only
through a separate index.jsonl mapping. An upward walk cannot reach a
sibling BY CONSTRUCTION, so before this the detector refused every such
tree -- correct on each individual verdict and useless in aggregate, which
is the same outcome as missing them entirely.

WHAT IS AND IS NOT RELAXED: only DISCOVERY. The requirement that a recipe
EXIST is unchanged, and a named path is verified on disk rather than
trusted -- a caller's assertion that a recipe exists is not evidence that
it does. The verdict also records that the recipe was SUPPLIED rather than
found, because "we located this" and "we were told this" deserve different
weight from whoever reads the evidence later.
"""

from __future__ import annotations

from pathlib import Path

from scitex_storage._regenerable import (
    NOT_REGENERABLE,
    REGENERABLE,
    is_regenerable,
)


def _target_env(root: Path) -> Path:
    """A pip install --target tree with no spec anywhere above it."""
    env = root / "runs" / "capsule-004" / "pylibs"
    (env / "numpy").mkdir(parents=True)
    (env / "numpy-2.5.1.dist-info").mkdir()
    return env


def test_without_a_supplied_recipe_a_sibling_corpus_is_unreachable(tmp_path):
    # The baseline this parameter exists to fix: the recipe is RIGHT THERE
    # in a sibling tree and the ancestor walk cannot see it.
    # Arrange
    env = _target_env(tmp_path)
    corpus = tmp_path / "corpus" / "capsule-004" / "input" / "environment"
    corpus.mkdir(parents=True)
    (corpus / "Dockerfile").write_text("FROM python:3.11\n")

    # Act
    verdict = is_regenerable(str(env), stop_at=str(tmp_path / "runs"))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_a_supplied_recipe_that_exists_makes_it_regenerable(tmp_path):
    # Arrange
    env = _target_env(tmp_path)
    corpus = tmp_path / "corpus" / "capsule-004" / "input" / "environment"
    corpus.mkdir(parents=True)
    dockerfile = corpus / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(dockerfile)],
    )

    # Assert
    assert verdict.verdict == REGENERABLE


def test_the_supplied_recipe_is_recorded_as_the_spec_path(tmp_path):
    # It must be auditable which file licensed the deletion.
    # Arrange
    env = _target_env(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    dockerfile = corpus / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(dockerfile)],
    )

    # Assert
    assert verdict.spec_path == str(dockerfile)


def test_the_evidence_says_the_recipe_was_SUPPLIED_not_found(tmp_path):
    # "We located this" and "we were told this" deserve different weight.
    # Arrange
    env = _target_env(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    dockerfile = corpus / "Dockerfile"
    dockerfile.write_text("FROM python:3.11\n")

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(dockerfile)],
    )

    # Assert
    assert "SUPPLIED BY THE CALLER" in verdict.evidence


def test_a_NAMED_recipe_that_does_not_exist_is_still_refused(tmp_path):
    # THE LOAD-BEARING TEST. A caller's assertion that a recipe exists is
    # not evidence that it does -- and this is not hypothetical: the whole
    # cohort-A clearance was withdrawn this morning because a documented
    # corpus path turned out not to exist on disk.
    # Arrange
    env = _target_env(tmp_path)
    ghost = tmp_path / "corpus" / "capsule-004" / "Dockerfile"

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(ghost)],
    )

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_the_refusal_says_the_named_recipe_did_not_resolve(tmp_path):
    # So the caller fixes their path rather than concluding the tree is
    # precious.
    # Arrange
    env = _target_env(tmp_path)
    ghost = tmp_path / "nowhere" / "Dockerfile"

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(ghost)],
    )

    # Assert
    assert "does not resolve" in verdict.evidence


def test_a_directory_named_as_a_recipe_does_not_count(tmp_path):
    # A recipe is a FILE. Accepting a directory would let a caller point at
    # anything that happens to exist.
    # Arrange
    env = _target_env(tmp_path)
    not_a_file = tmp_path / "corpus"
    not_a_file.mkdir()

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(not_a_file)],
    )

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_the_first_EXISTING_candidate_wins_not_the_first_named(tmp_path):
    # A caller may offer several plausible locations; a stale one first in
    # the list must not shadow a real one behind it.
    # Arrange
    env = _target_env(tmp_path)
    real = tmp_path / "corpus" / "Dockerfile"
    real.parent.mkdir(parents=True)
    real.write_text("FROM python:3.11\n")
    stale = tmp_path / "old-corpus" / "Dockerfile"

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(stale), str(real)],
    )

    # Assert
    assert verdict.spec_path == str(real)


def test_a_discovered_recipe_still_reads_as_discovered(tmp_path):
    # The provenance note must not mislabel a normally-found spec.
    # Arrange
    env = _target_env(tmp_path)
    (tmp_path / "runs" / "capsule-004" / "requirements.txt").write_text("numpy\n")

    # Act
    verdict = is_regenerable(str(env), stop_at=str(tmp_path / "runs"))

    # Assert
    assert "found by the ancestor walk" in verdict.evidence


def test_a_discovered_recipe_wins_over_a_supplied_one(tmp_path):
    # Discovery is stronger evidence than assertion, so it is preferred
    # when both are available.
    # Arrange
    env = _target_env(tmp_path)
    discovered = tmp_path / "runs" / "capsule-004" / "requirements.txt"
    discovered.write_text("numpy\n")
    supplied = tmp_path / "corpus" / "Dockerfile"
    supplied.parent.mkdir(parents=True)
    supplied.write_text("FROM python:3.11\n")

    # Act
    verdict = is_regenerable(
        str(env),
        stop_at=str(tmp_path / "runs"),
        extra_spec_paths=[str(supplied)],
    )

    # Assert
    assert verdict.spec_path == str(discovered)

# EOF
