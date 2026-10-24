"""Unit tests for scitex_storage._transfer._retention (restorable staging).

Pure local-filesystem behavior: stage a tree aside (atomic rename, no
copy), sweep it after expiry, refuse anything that is not provably ours.
HOME is sandboxed per test (save/set/restore, never a mock) because the
registry lives under ~/.scitex.
"""

from __future__ import annotations

import json
import os

import pytest

from scitex_storage._transfer import _retention as retention


@pytest.fixture
def sandbox_home(tmp_path):
    """Point $HOME at tmp_path; restore afterwards. No mocks involved."""
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    try:
        yield tmp_path
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def _tree(root):
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.bin").write_bytes(b"x" * 64)
    (root / "top.bin").write_bytes(b"y" * 32)
    return root


def test_stage_moves_the_tree_aside_atomically(sandbox_home):
    # Arrange
    src = _tree(sandbox_home / "work")
    # Act
    record = retention.stage_for_retention(src)
    # Assert -- source gone, bytes intact under staging, registry knows it.
    from pathlib import Path

    staged = Path(record.staged_path)
    result = (
        src.exists(),
        (staged / "sub" / "a.bin").read_bytes(),
        staged.parent.name,
        record.original_path,
    )
    assert result == (False, b"x" * 64, ".stx-restorable", str(src))


def test_stage_missing_source_is_a_loud_not_found(sandbox_home):
    # Arrange
    missing = sandbox_home / "gone"
    # Act
    raised = None
    try:
        retention.stage_for_retention(missing)
    except FileNotFoundError as exc:
        raised = exc
    # Assert
    assert raised is not None


def test_sweep_before_expiry_retains_everything(sandbox_home):
    # Arrange
    src = _tree(sandbox_home / "work")
    staged = retention.stage_for_retention(src).staged_path
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert
    from pathlib import Path

    result = (report.removed, report.retained, Path(staged).is_dir())
    assert result == ([], [staged], True)


def test_sweep_after_expiry_removes_and_prunes_the_registry(sandbox_home):
    # Arrange
    src = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(src, retention_days=0)
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert
    from pathlib import Path

    result = (
        report.removed,
        Path(record.staged_path).exists(),
        retention._read_registry(),
    )
    assert result == ([record.staged_path], False, [])


def test_sweep_dry_run_reports_without_touching(sandbox_home):
    # Arrange
    src = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(src, retention_days=0)
    # Act
    report = retention.sweep_restorable(dry_run=True)
    # Assert
    from pathlib import Path

    result = (report.removed, Path(record.staged_path).is_dir(), len(retention._read_registry()))
    assert result == ([record.staged_path], True, 1)


def test_sweep_refuses_a_path_outside_staging(sandbox_home):
    # Arrange -- a hand-edited registry entry pointing at user data.
    victim = sandbox_home / "precious.txt"
    victim.write_text("keep me")
    retention._write_registry([{
        "staged_path": str(victim),
        "original_path": str(victim),
        "staged_at": 0.0,
        "delete_after": 0.0,
    }])
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert -- refused, kept, and the file is untouched.
    result = (report.refused, report.removed, victim.read_text())
    assert result == ([str(victim)], [], "keep me")


def test_sweep_refuses_a_tree_whose_marker_is_gone(sandbox_home):
    # Arrange -- someone repurposed the staged directory.
    import shutil

    src = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(src, retention_days=0)
    from pathlib import Path

    staged = Path(record.staged_path)
    (staged / retention.MARKER_FILENAME).unlink()
    (staged / "intruder.txt").write_text("mine now")
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert
    result = (report.refused, staged.is_dir(), retention._read_registry() != [])
    assert result == ([record.staged_path], True, True)
    shutil.rmtree(staged, ignore_errors=True)


def test_sweep_prunes_an_already_gone_tree(sandbox_home):
    # Arrange
    import shutil

    src = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(src, retention_days=0)
    from pathlib import Path

    shutil.rmtree(record.staged_path)
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert
    result = (report.pruned, retention._read_registry())
    assert result == ([record.staged_path], [])


def test_stage_and_sweep_a_single_file(sandbox_home):
    # Arrange
    src = sandbox_home / "note.txt"
    src.write_text("hello")
    # Act
    record = retention.stage_for_retention(src, retention_days=0)
    report = retention.sweep_restorable(dry_run=False)
    # Assert -- staged aside with a sibling marker, then both removed.
    from pathlib import Path

    staged = Path(record.staged_path)
    marker = staged.parent / (staged.name + "." + retention.MARKER_FILENAME)
    result = (
        src.exists(),
        report.removed,
        staged.exists() or marker.exists(),
    )
    assert result == (False, [record.staged_path], False)
