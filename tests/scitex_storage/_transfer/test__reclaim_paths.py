"""Reclaim refuses dependent paths before moving or creating archive entries."""

import os

import pytest

from scitex_storage._transfer._reclaim import (
    ReclaimEntry,
    ReclaimPlan,
    apply_reclaim,
    plan_reclaim,
    restore_reclaim,
)


def _snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            result[key] = ("link", os.readlink(path))
        elif path.is_dir():
            result[key] = ("dir",)
        else:
            result[key] = ("file", path.read_bytes())
    return result


def _refusal(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    return ""


@pytest.fixture
def roots(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child.bin"
    child.write_bytes(b"owned child")
    sibling = tmp_path / "sibling.bin"
    sibling.write_bytes(b"owned sibling")
    try:
        yield tmp_path, parent, child, sibling
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


@pytest.fixture(params=["parent-first", "child-first", "directory", "parent-alias"])
def nested_sources(roots, request):
    root, parent, child, sibling = roots
    if request.param == "child-first":
        sources = [child, parent]
    elif request.param == "directory":
        subdir = parent / "subdir"
        subdir.mkdir()
        (subdir / "payload").write_bytes(b"nested")
        sources = [parent, subdir]
    elif request.param == "parent-alias":
        alias = root / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        sources = [parent, alias / child.name]
    else:
        sources = [parent, child]
    return root, sources


def test_plan_refuses_overlapping_sources_without_changes(nested_sources):
    # Arrange
    root, sources = nested_sources
    before = _snapshot(root)
    # Act
    error = _refusal(lambda: plan_reclaim(sources, run_id="nested"))
    # Assert
    assert ("sources overlap" in error, _snapshot(root)) == (True, before)


def test_apply_refuses_legacy_overlapping_sources_before_first_move(nested_sources):
    # Arrange
    root, sources = nested_sources
    plan = ReclaimPlan(
        "legacy",
        None,
        [
            ReclaimEntry(str(source), str(root / "archive" / str(index)), 1, 1)
            for index, source in enumerate(sources)
        ],
    )
    before = _snapshot(root)
    # Act
    error = _refusal(lambda: apply_reclaim(plan))
    # Assert
    assert ("sources overlap" in error, _snapshot(root)) == (True, before)


@pytest.fixture(params=["self", "other-source", "directory-alias"])
def embedded_archive(roots, request):
    root, parent, child, sibling = roots
    sources = [parent]
    archive = parent / "archive"
    if request.param == "other-source":
        sources = [sibling, parent]
    elif request.param == "directory-alias":
        alias = root / "alias"
        alias.symlink_to(parent, target_is_directory=True)
        archive = alias / "archive"
    return root, sources, archive


def test_plan_refuses_archive_inside_any_selected_tree(embedded_archive):
    # Arrange
    root, sources, archive = embedded_archive
    before = _snapshot(root)
    # Act
    error = _refusal(
        lambda: plan_reclaim(sources, run_id="embedded", archive_root=str(archive))
    )
    # Assert
    assert ("inside a selected source" in error, _snapshot(root)) == (True, before)


@pytest.mark.parametrize("reverse", [False, True])
def test_apply_refuses_nested_legacy_destinations(roots, reverse):
    # Arrange
    root, parent, child, sibling = roots
    destinations = [root / "archive" / "parent", root / "archive" / "parent/child"]
    plan = ReclaimPlan(
        "nested-dest",
        None,
        [
            ReclaimEntry(str(source), str(destination), 1, 1)
            for source, destination in zip(
                [child, sibling], destinations[:: (-1 if reverse else 1)]
            )
        ],
    )
    before = _snapshot(root)
    # Act
    error = _refusal(lambda: apply_reclaim(plan))
    # Assert
    assert ("destinations overlap" in error, _snapshot(root)) == (True, before)


def test_external_final_symlink_is_a_distinct_entry_and_round_trips(roots):
    # Arrange
    root, parent, child, sibling = roots
    link = root / "link"
    link.symlink_to(parent, target_is_directory=True)
    before = _snapshot(parent)
    plan = plan_reclaim([parent, link], run_id="distinct-link")
    # Act
    apply_reclaim(plan)
    restore_reclaim(plan.run_id)
    # Assert
    assert (_snapshot(parent), link.is_symlink(), os.readlink(link)) == (
        before,
        True,
        str(parent),
    )


def test_cli_reports_overlap_as_clean_error(roots):
    # Arrange
    from click.testing import CliRunner

    from scitex_storage._cli import main

    root, parent, child, sibling = roots
    before = _snapshot(root)
    # Act
    result = CliRunner().invoke(
        main, ["reclaim", str(parent), str(child), "--json", "--yes"]
    )
    # Assert
    assert (
        result.exit_code,
        "Cannot plan reclaim" in result.output,
        "Traceback" in result.output,
        _snapshot(root),
    ) == (1, True, False, before)


@pytest.fixture(params=["sources", "destinations"])
def overlapping_manifest(roots, request):
    from scitex_storage._transfer._reclaim import ReclaimManifest, _write_manifest

    root, parent, child, sibling = roots
    if request.param == "sources":
        originals = [root / "restore-one", root / "restore-two"]
        archives = [parent, child]
    else:
        originals = [root / "restore", root / "restore/nested"]
        archives = [child, sibling]
    entries = [ReclaimEntry(str(a), str(b), 1, 1) for a, b in zip(originals, archives)]
    _write_manifest(ReclaimManifest("overlap-restore", 0, None, entries))
    return root


def test_restore_refuses_overlapping_manifest_before_first_move(overlapping_manifest):
    # Arrange
    root = overlapping_manifest
    before = _snapshot(root)
    # Act
    error = _refusal(lambda: restore_reclaim("overlap-restore"))
    # Assert
    assert ("overlap" in error, _snapshot(root)) == (True, before)
