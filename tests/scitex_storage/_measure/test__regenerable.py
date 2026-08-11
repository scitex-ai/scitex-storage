"""Unit tests for scitex_storage._regenerable.

Every case is a real condition from Spartan punim0264 at 97% inode usage,
encoded so the two expensive mistakes cannot recur silently:

* an environment found by NAME rather than by structure (under-matches,
  leaves most inodes in place, and looks like it worked)
* an environment called "regenerable" with no recipe to rebuild it
  (deletes the only copy)

Everything here runs against a real tmp_path, so none of it needs
`monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import os

import pytest

from scitex_storage._measure._regenerable import (
    COULD_NOT_LOOK,
    NOT_REGENERABLE,
    REGENERABLE,
    RegenerableVerdict,
    detect_environment,
    find_spec,
    is_regenerable,
)


def _make_venv(root, name):
    """A virtualenv identified only by its pyvenv.cfg, under any name."""
    env = root / name
    env.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return env


def _make_conda(root, name):
    env = root / name
    (env / "conda-meta").mkdir(parents=True)
    return env


# --- structural detection, NOT name-based --------------------------------
@pytest.mark.parametrize(
    "name", ["rsandbox", "mamba", "pylibs", "myenv", "mmroot", "mm_root", "venv", "renv"]
)
def test_every_observed_environment_name_is_detected(tmp_path, name):
    # Arrange -- the eight names actually seen under punim0264.
    _make_venv(tmp_path, name)

    # Act
    ecosystem, _marker = detect_environment(str(tmp_path / name))

    # Assert
    assert ecosystem == "python-venv"


def test_a_directory_named_like_an_environment_but_empty_is_not_one(tmp_path):
    # Arrange -- the inverse error: name says venv, structure says nothing.
    decoy = tmp_path / "venv"
    decoy.mkdir()

    # Act
    ecosystem, _marker = detect_environment(str(decoy))

    # Assert
    assert ecosystem is None


def test_site_packages_is_found_despite_the_python_version_component(tmp_path):
    # Arrange
    sp = tmp_path / "oddname" / "lib" / "python3.11" / "site-packages"
    sp.mkdir(parents=True)

    # Act
    ecosystem, _marker = detect_environment(str(tmp_path / "oddname"))

    # Assert
    assert ecosystem == "python-venv"


def test_a_conda_environment_is_detected_by_conda_meta(tmp_path):
    # Arrange
    _make_conda(tmp_path, "mamba")

    # Act
    ecosystem, _marker = detect_environment(str(tmp_path / "mamba"))

    # Assert
    assert ecosystem == "conda"


def test_the_marker_that_fired_is_reported_for_audit(tmp_path):
    # Arrange
    _make_venv(tmp_path, "rsandbox")

    # Act
    _ecosystem, marker = detect_environment(str(tmp_path / "rsandbox"))

    # Assert
    assert marker == "pyvenv.cfg"


# --- the recipe requirement ----------------------------------------------
def test_an_environment_with_no_spec_anywhere_is_not_regenerable(tmp_path):
    # Arrange -- a venv that is the ONLY copy: deleting it loses the work.
    _make_venv(tmp_path, "rsandbox")

    # Act
    verdict = is_regenerable(str(tmp_path / "rsandbox"), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_the_missing_recipe_is_named_rather_than_just_refused(tmp_path):
    # Arrange
    _make_venv(tmp_path, "rsandbox")

    # Act
    verdict = is_regenerable(str(tmp_path / "rsandbox"), stop_at=str(tmp_path))

    # Assert
    assert "NO rebuild spec" in verdict.evidence


def test_a_spec_beside_the_environment_makes_it_regenerable(tmp_path):
    # Arrange -- the capsule convention: environment.yml next to mamba/.
    _make_conda(tmp_path, "mamba")
    (tmp_path / "environment.yml").write_text("name: capsule\n")

    # Act
    verdict = is_regenerable(str(tmp_path / "mamba"), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == REGENERABLE


def test_the_regenerable_verdict_names_the_recipe_it_relies_on(tmp_path):
    # Arrange
    _make_conda(tmp_path, "mamba")
    (tmp_path / "environment.yml").write_text("name: capsule\n")

    # Act
    verdict = is_regenerable(str(tmp_path / "mamba"), stop_at=str(tmp_path))

    # Assert
    assert verdict.spec_path == str(tmp_path / "environment.yml")


def test_a_spec_for_a_different_ecosystem_does_not_count(tmp_path):
    # Arrange -- requirements.txt cannot rebuild an renv library.
    lib = tmp_path / "renv" / "renv" / "library"
    lib.mkdir(parents=True)
    (tmp_path / "requirements.txt").write_text("numpy\n")

    # Act
    verdict = is_regenerable(str(tmp_path / "renv"), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_the_ancestor_walk_stops_at_the_declared_boundary(tmp_path):
    # Arrange -- a spec ABOVE the boundary belongs to another project and
    # must not license deleting this one.
    outer = tmp_path / "outer"
    capsule = outer / "capsule"
    _make_venv(capsule, "pylibs")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='unrelated'\n")

    # Act
    verdict = is_regenerable(str(capsule / "pylibs"), stop_at=str(outer))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


def test_a_spec_in_an_ancestor_within_the_boundary_is_credited(tmp_path):
    # Arrange
    capsule = tmp_path / "capsule"
    _make_venv(capsule, "pylibs")
    (capsule / "requirements.txt").write_text("numpy\n")

    # Act
    found = find_spec(str(capsule / "pylibs"), "python-venv", stop_at=str(tmp_path))

    # Assert
    assert found == str(capsule / "requirements.txt")


# --- the third state ------------------------------------------------------
def test_an_absent_path_is_could_not_look_rather_than_nothing_here(tmp_path):
    # Arrange
    missing = tmp_path / "gone"

    # Act
    verdict = is_regenerable(str(missing))

    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_an_unreadable_directory_is_could_not_look_not_not_an_environment(tmp_path):
    # Arrange -- the failure mode always produces the convenient answer.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)

    # Act
    verdict = is_regenerable(str(locked))
    locked.chmod(0o755)  # so pytest can clean up

    # Assert
    assert verdict.verdict == COULD_NOT_LOOK


def test_a_plain_results_directory_is_not_regenerable(tmp_path):
    # Arrange -- the 24-inode `results` dir must never be swept.
    results = tmp_path / "results"
    results.mkdir()
    (results / "figure_01.png").write_bytes(b"\x89PNG")

    # Act
    verdict = is_regenerable(str(results), stop_at=str(tmp_path))

    # Assert
    assert verdict.verdict == NOT_REGENERABLE


# --- the validator --------------------------------------------------------
def test_regenerable_without_a_named_recipe_is_refused_at_construction():
    # Arrange -- deleting on an unnamed claim is unrecoverable.
    kwargs = dict(
        path="/x",
        verdict=REGENERABLE,
        ecosystem="conda",
        marker="conda-meta",
        spec_path=None,
        evidence="looked fine to me",
    )

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        RegenerableVerdict(**kwargs)


def test_a_verdict_without_evidence_is_refused():
    # Arrange
    kwargs = dict(
        path="/x",
        verdict=NOT_REGENERABLE,
        ecosystem=None,
        marker=None,
        spec_path=None,
        evidence="   ",
    )

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        RegenerableVerdict(**kwargs)


def test_an_unknown_verdict_is_refused():
    # Arrange
    kwargs = dict(
        path="/x",
        verdict="probably-junk",
        ecosystem=None,
        marker=None,
        spec_path=None,
        evidence="hand-wave",
    )

    # Act
    raised = pytest.raises(ValueError)

    # Assert
    with raised:
        RegenerableVerdict(**kwargs)

# EOF
