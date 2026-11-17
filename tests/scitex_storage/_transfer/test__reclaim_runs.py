"""Single-use reclaim records and real same-run interprocess locking."""

import json
import os
import subprocess
import sys

import pytest

from scitex_storage._transfer._durable import file_lock
from scitex_storage._transfer._reclaim import (
    _manifest_path,
    apply_reclaim,
    load_manifest,
    plan_reclaim,
    restore_reclaim,
)


def _attempt(operation):
    try:
        operation()
    except OSError as error:
        return type(error).__name__
    return "success"


@pytest.fixture
def roots(tmp_path):
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path / "home")
    sources = [tmp_path / "first", tmp_path / "second"]
    for index, source in enumerate(sources):
        source.write_bytes(f"owned-{index}".encode())
    try:
        yield tmp_path, sources
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


@pytest.fixture(params=[False, True], ids=["archived", "restored"])
def recorded_run(roots, request):
    root, sources = roots
    stale = plan_reclaim([sources[1]], run_id="recorded")
    first = apply_reclaim(plan_reclaim(sources[:1], run_id="recorded"))
    if request.param:
        restore_reclaim(first.run_id)
    path = _manifest_path(first.run_id)
    return root, sources, stale, path, path.read_bytes()


def test_planning_cannot_reuse_completed_or_restored_run(recorded_run):
    # Arrange
    root, sources, stale, path, before = recorded_run
    # Act
    result = _attempt(lambda: plan_reclaim(sources[1:], run_id="recorded"))
    # Assert
    assert (result, sources[1].read_bytes(), path.read_bytes()) == (
        "FileExistsError",
        b"owned-1",
        before,
    )


def test_old_plan_cannot_replace_another_runs_record(recorded_run):
    # Arrange
    root, sources, stale, path, before = recorded_run
    # Act
    result = _attempt(lambda: apply_reclaim(stale))
    # Assert
    assert (
        result,
        sources[1].read_bytes(),
        path.read_bytes(),
        load_manifest("recorded").entries[0].original,
    ) == ("FileExistsError", b"owned-1", before, str(sources[0]))


@pytest.fixture(params=["manifest", "started", "dangling-manifest", "dangling-started"])
def existing_record(roots, request):
    root, sources = roots
    path = _manifest_path("occupied")
    if "started" in request.param:
        path = path.with_suffix(".started")
    path.parent.mkdir(parents=True)
    if request.param.startswith("dangling"):
        path.symlink_to(root / "missing")
        before = os.readlink(path)
    else:
        path.write_bytes(b"not valid JSON")
        before = path.read_bytes()
    return root, sources, path, before


def test_malformed_or_dangling_records_still_reserve_run_id(existing_record):
    # Arrange
    root, sources, path, before = existing_record
    # Act
    result = _attempt(lambda: plan_reclaim(sources[:1], run_id="occupied"))
    after = os.readlink(path) if path.is_symlink() else path.read_bytes()
    # Assert
    assert (result, after, sources[0].read_bytes()) == (
        "FileExistsError",
        before,
        b"owned-0",
    )


def test_move_failure_retains_intent_and_refuses_run_reuse(roots):
    # Arrange -- real mkdir failure independent of privilege, no replaced functions.
    root, sources = roots
    blocker = root / "not-a-directory"
    blocker.write_bytes(b"keep")
    plan = plan_reclaim(sources[:1], run_id="failed", archive_root=str(blocker))
    # Act
    result = _attempt(lambda: apply_reclaim(plan))
    intent = json.loads(_manifest_path("failed").with_suffix(".started").read_text())
    retry = _attempt(lambda: plan_reclaim(sources[1:], run_id="failed"))
    # Assert
    assert (
        result,
        retry,
        intent["entries"][0]["original"],
        sources[0].read_bytes(),
        blocker.read_bytes(),
        _manifest_path("failed").exists(),
    ) == (
        "NotADirectoryError",
        "FileExistsError",
        str(sources[0]),
        b"owned-0",
        b"keep",
        False,
    )


@pytest.mark.parametrize("same_run", [True, False])
def test_cooperating_process_lock_is_per_run_and_nonblocking(roots, same_run):
    # Arrange -- actual OS lock in this process, actual apply in a second process.
    root, sources = roots
    run_id = "locked" if same_run else "other"
    code = """
import json,sys
from scitex_storage._transfer._reclaim import plan_reclaim,apply_reclaim
try:
 apply_reclaim(plan_reclaim([sys.argv[1]],run_id=sys.argv[2]));result='success'
except OSError as error:result=type(error).__name__
print(json.dumps({'result':result}))
"""
    # Act
    with file_lock(_manifest_path("locked").with_suffix(".lock")):
        result = subprocess.run(
            [sys.executable, "-c", code, str(sources[0]), run_id],
            capture_output=True,
            text=True,
            timeout=15,
        )
    # Assert
    assert (
        result.returncode,
        json.loads(result.stdout)["result"],
        sources[0].exists(),
        _manifest_path(run_id).with_suffix(".started").exists(),
    ) == (0, "BlockingIOError" if same_run else "success", same_run, not same_run)


def test_symlink_lock_is_refused_without_following_it(roots):
    # Arrange
    root, sources = roots
    target = root / "lock-target"
    target.write_bytes(b"preserved")
    lock = _manifest_path("linked-lock").with_suffix(".lock")
    lock.parent.mkdir(parents=True)
    lock.symlink_to(target)
    plan = plan_reclaim(sources[:1], run_id="linked-lock")
    # Act
    result = _attempt(lambda: apply_reclaim(plan))
    # Assert
    assert (
        result,
        target.read_bytes(),
        sources[0].read_bytes(),
        lock.is_symlink(),
        lock.with_suffix(".started").exists(),
    ) == ("OSError", b"preserved", b"owned-0", True, False)


def test_cli_apply_failure_is_readable_and_retains_attempt_record(roots):
    # Arrange
    from click.testing import CliRunner

    from scitex_storage._cli import main

    root, sources = roots
    blocker = root / "archive-file"
    blocker.write_bytes(b"unchanged")
    # Act
    result = CliRunner().invoke(
        main,
        ["reclaim", str(sources[0]), "--archive-root", str(blocker), "--yes", "--json"],
    )
    records = list((root / "home").rglob("*.started"))
    # Assert
    assert (
        result.exit_code,
        "Cannot apply reclaim" in result.output,
        "Traceback" in result.output,
        len(records),
        sources[0].read_bytes(),
        blocker.read_bytes(),
    ) == (1, True, False, 1, b"owned-0", b"unchanged")


@pytest.fixture(params=["direct", "parent-alias", "symlinked-state"])
def record_tree(roots, request):
    root, sources = roots
    home = root / "home"
    home.mkdir()
    (home / "owned").write_bytes(b"keep home")
    source = home
    if request.param == "parent-alias":
        alias = root / "alias"
        alias.symlink_to(root, target_is_directory=True)
        source = alias / "home"
    if request.param == "symlinked-state":
        target = root / "shared-state"
        target.mkdir()
        (home / ".scitex").symlink_to(target, target_is_directory=True)
    return home, source, sorted(p.name for p in home.iterdir())


def test_planning_cannot_select_its_own_record_tree(record_tree):
    # Arrange
    home, source, names = record_tree
    # Act
    try:
        plan_reclaim([source], run_id="self-records")
        result = "accepted"
    except ValueError as error:
        result = str(error)
    # Assert
    assert (
        "contains its run records" in result,
        (home / "owned").read_bytes(),
        sorted(p.name for p in home.iterdir()),
    ) == (True, b"keep home", names)


def test_old_plan_cannot_move_its_record_tree(record_tree):
    # Arrange
    from scitex_storage._transfer._reclaim import ReclaimEntry, ReclaimPlan

    home, source, names = record_tree
    destination = home.parent / "archive" / "legacy-home"
    plan = ReclaimPlan(
        "legacy-home", None, [ReclaimEntry(str(source), str(destination), 1, 1)]
    )
    # Act
    try:
        apply_reclaim(plan)
        result = "accepted"
    except ValueError as error:
        result = str(error)
    # Assert
    assert (
        "contains its run records" in result,
        (home / "owned").read_bytes(),
        sorted(p.name for p in home.iterdir()),
        destination.exists(),
    ) == (True, b"keep home", names, False)
