"""Restore preflights the complete run before moving its first entry."""

import json
import os
from pathlib import Path

import pytest

from scitex_storage._transfer._reclaim import (
    apply_reclaim,
    plan_reclaim,
    restore_reclaim,
)


def state(path):
    if path.is_symlink():
        return "link", os.readlink(path)
    if path.is_file():
        return "file", path.read_bytes()
    return "present" if path.exists() else "missing"


def refusal(operation):
    try:
        operation()
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        return type(error)
    return None


@pytest.fixture
def archived_run(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    sources = [tmp_path / "original" / name for name in ("first.bin", "last.bin")]
    sources[0].parent.mkdir()
    for index, source in enumerate(sources):
        source.write_bytes(f"owned-{index}".encode())
    try:
        plan = plan_reclaim(
            sources, run_id="restore-preflight", archive_root=str(tmp_path / "archive")
        )
        apply_reclaim(plan)
        manifest = (
            home
            / ".scitex/scitex-storage/runtime/reclaim-manifests/restore-preflight.json"
        )
        yield sources, [Path(entry.archived) for entry in plan.entries], manifest
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


@pytest.fixture(params=["occupied", "dangling", "missing", "repeated", "alias"])
def obstructed_run(archived_run, request, tmp_path):
    sources, archived, manifest = archived_run
    error = FileExistsError
    if request.param == "occupied":
        sources[1].write_bytes(b"new occupant")
    elif request.param == "dangling":
        sources[1].symlink_to(tmp_path / "missing-target")
    elif request.param == "missing":
        archived[1].unlink()
        error = FileNotFoundError
    else:
        payload = json.loads(manifest.read_text())
        target = sources[0]
        if request.param == "alias":
            alias = tmp_path / "original-alias"
            alias.symlink_to(sources[0].parent, target_is_directory=True)
            target = alias / sources[0].name
        payload["entries"][1]["original"] = str(target)
        manifest.write_text(json.dumps(payload))
        error = ValueError
    paths = sources + archived + [manifest]
    return paths, tuple(state(path) for path in paths), error


def test_restore_refuses_the_whole_obstructed_run_before_any_move(obstructed_run):
    # Arrange -- a later known failure must not return the first archive entry.
    paths, before, expected = obstructed_run
    # Act
    error = refusal(lambda: restore_reclaim("restore-preflight"))
    # Assert
    assert (error, tuple(state(path) for path in paths)) == (expected, before)


def test_restore_still_returns_every_entry_of_a_clear_run(archived_run):
    # Arrange
    sources, archived, manifest = archived_run
    # Act
    restored = restore_reclaim("restore-preflight")
    # Assert
    assert (
        [source.read_bytes() for source in sources],
        [path.exists() for path in archived],
        restored.restored,
    ) == ([b"owned-0", b"owned-1"], [False, False], True)
