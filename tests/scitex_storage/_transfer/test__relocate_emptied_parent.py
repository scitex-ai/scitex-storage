"""Regression (2026-11-16, business-repo reorganization): simulation vs apply drift.

The apply prunes folders emptied by a move; the simulation used to keep them, so
a later parent-merge planned a move of a folder that no longer existed and the
run stopped half-applied (rsync exit 23). The simulation must prune the same way.

NO MOCKS (PA-306): real temp trees and a real sandboxed HOME.
"""

import os

import pytest

from scitex_storage._transfer._relocate import apply_relocate, plan_relocate


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


@pytest.fixture
def kk_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "kk/programs/KKJ/case").mkdir(parents=True)
    (root / "kk/programs/KKJ/case/spec.pdf").write_bytes(b"spec")
    (root / "kk/programs/other.md").write_text("x")
    (root / "company/programs").mkdir(parents=True)
    (root / "company/programs/existing.md").write_text("y")
    spec = {
        "moves": [
            {"from": "kk/programs/KKJ/case", "to": "company/programs/tenders/case"},
            {"from": "kk", "to": "company"},
        ]
    }
    return root, spec


def test_emptied_folder_is_not_planned_as_a_move(kk_tree, sandbox_home):
    # Arrange
    root, spec = kk_tree
    # Act
    plan = plan_relocate(spec, root, run_id="r1")
    # Assert
    assert "kk/programs/KKJ" not in [o.src for o in plan.ops if o.kind == "move"]


def test_apply_completes_after_child_moved_out(kk_tree, sandbox_home):
    # Arrange
    root, spec = kk_tree
    plan = plan_relocate(spec, root, run_id="r1")
    # Act
    manifest = apply_relocate(plan)
    # Assert
    assert manifest.complete is True


def test_apply_leaves_no_source_tree_behind(kk_tree, sandbox_home):
    # Arrange
    root, spec = kk_tree
    # Act
    apply_relocate(plan_relocate(spec, root, run_id="r1"))
    # Assert
    assert not (root / "kk").exists()
