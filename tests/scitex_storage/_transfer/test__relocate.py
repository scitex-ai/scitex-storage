"""Unit tests for the relocate engine (simulate -> apply -> restore).

NO MOCKS (PA-306): real temp trees, real sandboxed HOME for manifests. The
properties pinned hardest: the dry run SIMULATES in order (a later move sees an
earlier one), a conflict changes NOTHING, identical files dedupe, file links are
refused, and restore returns the tree to exactly where it started.
"""

import os

import pytest

from scitex_storage._transfer._relocate import (
    RelocateConflict,
    apply_relocate,
    plan_relocate,
    restore_relocate,
)


@pytest.fixture
def sandbox_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev


def _write(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".old"]
        for f in filenames:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "repo"
    _write(root, "kk/a/one.txt", b"1")
    _write(root, "kk/a/two.txt", b"2")
    _write(root, "kk/b/three.txt", b"3")
    _write(root, "kk/~$lock.docx", b"junk")
    _write(root, "kk/b/file.pdf:Zone.Identifier", b"junk")
    return root


def test_later_move_sees_earlier_move(tree, sandbox_home):
    # Arrange -- child moved out first, then the parent is renamed.
    spec = {
        "moves": [{"from": "kk/a", "to": "personal/a"}, {"from": "kk", "to": "company"}]
    }
    # Act
    plan = plan_relocate(spec, tree, run_id="r1")
    # Assert -- the parent move is one op that no longer carries kk/a.
    assert [(o.src, o.dst) for o in plan.ops if o.kind == "move"] == [
        ("kk/a", "personal/a"),
        ("kk", "company"),
    ]


def test_dry_run_changes_nothing(tree, sandbox_home):
    # Arrange
    before = _snapshot(tree)
    # Act
    plan_relocate({"moves": [{"from": "kk", "to": "company"}]}, tree, run_id="r1")
    # Assert
    assert _snapshot(tree) == before


def test_junk_is_archived_not_deleted(tree, sandbox_home):
    # Arrange
    plan = plan_relocate({}, tree, run_id="r1")
    # Act
    apply_relocate(plan)
    # Assert -- junk left the tree but survives under the archive.
    assert (
        not (tree / "kk/~$lock.docx").exists()
        and (tree / ".old/relocate/r1/kk/~$lock.docx").read_bytes() == b"junk"
    )


def test_merge_into_existing_dir_dedupes_identical_files(tree, sandbox_home):
    # Arrange -- destination already has an identical copy of one.txt.
    _write(tree, "company/a/one.txt", b"1")
    spec = {"moves": [{"from": "kk/a", "to": "company/a"}]}
    # Act
    plan = plan_relocate(spec, tree, run_id="r1")
    # Assert
    assert [(o.src, o.reason) for o in plan.ops if o.reason == "duplicate"] == [
        ("kk/a/one.txt", "duplicate")
    ]


def test_merge_moves_new_files_and_removes_emptied_source(tree, sandbox_home):
    # Arrange
    _write(tree, "company/a/one.txt", b"1")
    plan = plan_relocate(
        {"moves": [{"from": "kk/a", "to": "company/a"}]}, tree, run_id="r1"
    )
    # Act
    apply_relocate(plan)
    # Assert
    assert (tree / "company/a/two.txt").read_bytes() == b"2" and not (
        tree / "kk/a"
    ).exists()


@pytest.fixture
def conflicting(tree):
    _write(tree, "company/a/one.txt", b"DIFFERENT")
    spec = {"moves": [{"from": "kk/b", "to": "company/b"}, {"from": "kk/a", "to": "company/a"}]}
    return tree, spec


def test_conflict_is_reported(conflicting, sandbox_home):
    # Arrange
    tree, spec = conflicting
    # Act
    plan = plan_relocate(spec, tree, run_id="r1")
    # Assert
    assert plan.conflicts == ["kk/a/one.txt -> company/a/one.txt"]


def test_conflict_refuses_apply(conflicting, sandbox_home):
    # Arrange
    tree, spec = conflicting
    plan = plan_relocate(spec, tree, run_id="r1")
    # Act
    # Assert
    with pytest.raises(RelocateConflict):
        apply_relocate(plan)


def test_conflict_leaves_tree_untouched(conflicting, sandbox_home):
    # Arrange
    tree, spec = conflicting
    before = _snapshot(tree)
    plan = plan_relocate(spec, tree, run_id="r1")
    # Act
    try:
        apply_relocate(plan)
    except RelocateConflict:
        pass
    # Assert -- not even the conflict-free first move happened.
    assert _snapshot(tree) == before


def test_file_link_is_refused(tree, sandbox_home):
    # Arrange
    spec = {"links": [{"path": "kk/link.txt", "target": "kk/a/one.txt"}]}
    # Act
    # Assert
    with pytest.raises(ValueError, match="directory"):
        plan_relocate(spec, tree, run_id="r1")


def test_directory_link_is_relative(tree, sandbox_home):
    # Arrange
    plan = plan_relocate(
        {"links": [{"path": "clients/x/deck", "target": "kk/a"}]}, tree, run_id="r1"
    )
    # Act
    apply_relocate(plan)
    # Assert
    assert os.readlink(tree / "clients/x/deck") == "../../kk/a"


def test_restore_round_trip_returns_tree_to_start(tree, sandbox_home):
    # Arrange
    _write(tree, "company/a/one.txt", b"1")
    before = _snapshot(tree)
    spec = {
        "moves": [
            {"from": "kk/a", "to": "company/a"},
            {"from": "kk", "to": "company/rest"},
        ],
        "drop": ["company/rest/b/three.txt"],
        "links": [{"path": "view/a", "target": "company/a"}],
    }
    apply_relocate(plan_relocate(spec, tree, run_id="r1"))
    # Act
    restore_relocate("r1")
    # Assert
    assert _snapshot(tree) == before


def test_move_into_itself_is_rejected(tree, sandbox_home):
    # Arrange
    spec = {"moves": [{"from": "kk", "to": "kk/sub"}]}
    # Act
    # Assert
    with pytest.raises(ValueError, match="into itself"):
        plan_relocate(spec, tree, run_id="r1")


def test_absolute_plan_path_is_rejected(tree, sandbox_home):
    # Arrange
    spec = {"moves": [{"from": "/etc", "to": "x"}]}
    # Act
    # Assert
    with pytest.raises(ValueError, match="relative"):
        plan_relocate(spec, tree, run_id="r1")
