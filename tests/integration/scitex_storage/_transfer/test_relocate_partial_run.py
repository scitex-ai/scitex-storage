"""Real filesystem controls for the documented partial relocation contract."""

import pytest

from scitex_storage._transfer._relocate import (
    apply_relocate,
    load_manifest,
    plan_relocate,
    restore_relocate,
)


@pytest.fixture
def partial_run(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.txt").write_bytes(b"first payload")
    second = root / "second.txt"
    second.write_bytes(b"second payload")
    plan = plan_relocate(
        {
            "moves": [
                {"from": "first.txt", "to": "moved/first.txt"},
                {"from": "second.txt", "to": "moved/second.txt"},
            ]
        },
        root,
        run_id="partial-run",
    )
    (root / "moved").mkdir()
    blocker = root / "moved/second.txt"
    blocker.write_bytes(b"new occupant")
    manifests = tmp_path / "manifests"
    with pytest.raises(FileExistsError):
        apply_relocate(plan, manifest_dir=manifests)
    return root, manifests


def test_partial_failure_retains_completed_move_and_incomplete_receipt(partial_run):
    # Arrange
    root, manifests = partial_run
    # Act
    receipt = load_manifest("partial-run", manifest_dir=manifests)
    observed = (
        receipt.complete,
        receipt.restored,
        [(op.kind, op.src, op.dst) for op in receipt.ops],
        (root / "first.txt").exists(),
        (root / "moved/first.txt").read_bytes(),
        (root / "moved/second.txt").read_bytes(),
        (root / "second.txt").read_bytes(),
    )
    # Assert
    assert observed == (
        False,
        False,
        [("move", "first.txt", "moved/first.txt")],
        False,
        b"first payload",
        b"new occupant",
        b"second payload",
    )


def test_partial_receipt_restores_only_recorded_operations(partial_run):
    # Arrange
    root, manifests = partial_run
    # Act
    restore_relocate("partial-run", manifest_dir=manifests)
    receipt = load_manifest("partial-run", manifest_dir=manifests)
    observed = (
        receipt.complete,
        receipt.restored,
        (root / "first.txt").read_bytes(),
        (root / "second.txt").read_bytes(),
        (root / "moved/first.txt").exists(),
        (root / "moved/second.txt").read_bytes(),
    )
    # Assert
    assert observed == (
        False,
        True,
        b"first payload",
        b"second payload",
        False,
        b"new occupant",
    )
