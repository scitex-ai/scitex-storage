"""Real filesystem controls for reclaim destination collisions."""

import os
from pathlib import Path

import pytest

from scitex_storage._transfer._reclaim import (
    ReclaimEntry,
    ReclaimPlan,
    _move_one,
    apply_reclaim,
    list_manifests,
    plan_reclaim,
    restore_reclaim,
)


def attempt(operation):
    try:
        operation()
    except (ValueError, OSError) as error:
        return type(error)
    return None


def contents(path):
    return path.read_bytes() if path.is_file() else None


@pytest.fixture
def roots(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    sources = []
    for index in range(2):
        source = tmp_path / str(index) / "report.txt"
        source.parent.mkdir()
        source.write_bytes(f"payload-{index}".encode())
        sources.append(source)
    try:
        yield sources, tmp_path / "archive"
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def test_plan_refuses_duplicate_destinations_without_moving_sources(roots):
    # Arrange
    sources, archive = roots
    # Act
    error = attempt(
        lambda: plan_reclaim(sources, run_id="run", archive_root=str(archive))
    )
    # Assert
    assert (error, [contents(p) for p in sources], archive.exists()) == (
        ValueError,
        [b"payload-0", b"payload-1"],
        False,
    )


def test_apply_refuses_a_legacy_colliding_plan_before_any_move(roots):
    # Arrange
    sources, archive = roots
    destination = archive / "run/report.txt"
    plan = ReclaimPlan(
        "run",
        str(archive),
        [ReclaimEntry(str(p), str(destination), p.stat().st_size, 1) for p in sources],
    )
    # Act
    error = attempt(lambda: apply_reclaim(plan))
    # Assert
    assert (
        error,
        [contents(p) for p in sources],
        archive.exists(),
        list_manifests(),
    ) == (ValueError, [b"payload-0", b"payload-1"], False, [])


@pytest.mark.parametrize("occupied", [0, 1])
def test_apply_checks_all_destinations_before_moving_any_source(roots, occupied):
    # Arrange
    sources, archive = roots
    sources[1] = sources[1].rename(sources[1].with_name("other.txt"))
    plan = plan_reclaim(sources, run_id="run", archive_root=str(archive))
    destination = Path(plan.entries[occupied].archived)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"existing occupant")
    # Act
    error = attempt(lambda: apply_reclaim(plan))
    # Assert
    assert (
        error,
        [contents(p) for p in sources],
        contents(destination),
        list_manifests(),
    ) == (FileExistsError, [b"payload-0", b"payload-1"], b"existing occupant", [])


def test_plan_refuses_a_dangling_destination_symlink(roots):
    # Arrange
    sources, archive = roots
    destination = archive / "run/report.txt"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(archive / "missing")
    # Act
    error = attempt(
        lambda: plan_reclaim(sources[:1], run_id="run", archive_root=str(archive))
    )
    # Assert
    assert (error, contents(sources[0]), destination.is_symlink()) == (
        FileExistsError,
        b"payload-0",
        True,
    )


def test_same_filesystem_publish_cannot_overwrite_a_late_occupant(roots):
    # Arrange
    sources, archive = roots
    plan = plan_reclaim(sources[:1], run_id="run", archive_root=str(archive))
    destination = Path(plan.entries[0].archived)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"late occupant")
    # Act
    error = attempt(lambda: _move_one(plan.entries[0]))
    # Assert
    assert (error, contents(sources[0]), contents(destination)) == (
        FileExistsError,
        b"payload-0",
        b"late occupant",
    )


def test_same_basenames_in_separate_adjacent_archives_round_trip(roots):
    # Arrange
    sources, _archive = roots
    plan = plan_reclaim(sources, run_id="run")
    # Act
    manifest = apply_reclaim(plan)
    restore_reclaim(manifest.run_id)
    # Assert
    assert (
        [contents(p) for p in sources],
        len({e.archived for e in manifest.entries}),
    ) == ([b"payload-0", b"payload-1"], 2)
