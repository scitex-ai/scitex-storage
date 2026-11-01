"""Unit tests for scitex_storage._transfer._retention (restorable staging).

Pure local-filesystem behavior: stage a tree aside (atomic rename, no
copy), sweep it after expiry, refuse anything that is not provably ours.
HOME is sandboxed per test (save/set/restore, never a mock) because the
registry lives under ~/.scitex.
"""

from __future__ import annotations

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


def test_sweep_defaults_to_dry_run(sandbox_home):
    # Arrange -- the API, like the CLI, removes nothing unless asked.
    src = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(src, retention_days=0)
    # Act
    report = retention.sweep_restorable()
    # Assert
    from pathlib import Path

    result = (report.removed, Path(record.staged_path).is_dir())
    assert result == ([record.staged_path], True)


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

    result = (
        report.removed,
        Path(record.staged_path).is_dir(),
        len(retention._read_registry()),
    )
    assert result == ([record.staged_path], True, 1)


def test_sweep_refuses_a_path_outside_staging(sandbox_home):
    # Arrange -- a hand-edited registry entry pointing at user data.
    victim = sandbox_home / "precious.txt"
    victim.write_text("keep me")
    retention._write_registry(
        [
            {
                "staged_path": str(victim),
                "original_path": str(victim),
                "staged_at": 0.0,
                "delete_after": 0.0,
            }
        ]
    )
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
    retention._marker_path_for(staged).unlink()
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


def test_restore_staged_preserves_payload_and_unregisters(sandbox_home):
    # Arrange
    source = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(source)
    # Act
    restored = retention.restore_staged(record.staged_path)
    # Assert
    assert (
        restored,
        (restored / "top.bin").read_bytes(),
        retention._read_registry(),
    ) == (source, b"y" * 32, [])


def test_restore_collision_keeps_both_trees(sandbox_home):
    # Arrange
    source = _tree(sandbox_home / "work")
    record = retention.stage_for_retention(source)
    source.mkdir()
    (source / "new.bin").write_bytes(b"new")
    # Act
    refused = False
    try:
        retention.restore_staged(record.staged_path)
    except FileExistsError:
        refused = True
    # Assert
    from pathlib import Path

    assert (
        refused,
        (source / "new.bin").read_bytes(),
        (Path(record.staged_path) / "top.bin").read_bytes(),
    ) == (True, b"new", b"y" * 32)


def test_changed_staged_content_is_not_deleted(sandbox_home):
    # Arrange
    from pathlib import Path

    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    staged = Path(record.staged_path)
    (staged / "top.bin").write_bytes(b"z" * 32)
    # Act
    report = retention.sweep_restorable(dry_run=False)
    # Assert
    assert (report.removed, report.refused, (staged / "top.bin").read_bytes()) == (
        [],
        [str(staged)],
        b"z" * 32,
    )


def test_dry_run_keeps_registry_marker_and_lock_bytes_unchanged(sandbox_home):
    # Arrange
    from pathlib import Path

    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    paths = [
        retention._registry_path(),
        retention._lock_path(),
        retention._marker_path_for(Path(record.staged_path)),
    ]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    # Act
    report = retention.sweep_restorable()
    after = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
    # Assert
    assert (after, report.removed, Path(record.staged_path).exists()) == (
        before,
        [record.staged_path],
        True,
    )


def test_first_dry_run_creates_no_state_directory(sandbox_home):
    # Arrange
    state = sandbox_home / ".scitex"
    # Act
    report = retention.sweep_restorable()
    # Assert
    assert (state.exists(), report.removed) == (False, [])


def test_sweep_obeys_item_budget(sandbox_home):
    # Arrange
    first = retention.stage_for_retention(
        _tree(sandbox_home / "first"), retention_days=0
    )
    second = retention.stage_for_retention(
        _tree(sandbox_home / "second"), retention_days=0
    )
    # Act
    report = retention.sweep_restorable(dry_run=False, max_items=1)
    # Assert
    assert (report.removed, report.retained) == (
        [first.staged_path],
        [second.staged_path],
    )


def test_sweep_obeys_byte_budget(sandbox_home):
    # Arrange
    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    # Act
    report = retention.sweep_restorable(dry_run=False, max_bytes=1)
    # Assert
    assert (report.removed, report.retained) == ([], [record.staged_path])


def test_cancelled_sweep_keeps_expired_tree(sandbox_home):
    # Arrange
    import threading

    event = threading.Event()
    event.set()
    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    # Act
    report = retention.sweep_restorable(dry_run=False, cancelled=event.is_set)
    # Assert
    assert (report.removed, report.retained) == ([], [record.staged_path])


def test_pending_crash_intent_is_recoverable_but_not_deletable(sandbox_home):
    # Arrange
    from pathlib import Path

    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    entries = retention._read_registry()
    entries[0]["state"] = "pending"
    entries[0]["staged_at"] -= 1
    entries[0]["delete_after"] -= 1
    retention._write_registry(entries)
    # Act
    report = retention.sweep_restorable(dry_run=False)
    restored = retention.restore_staged(record.staged_path)
    # Assert
    assert (
        report.removed,
        report.refused,
        (restored / "top.bin").read_bytes(),
        Path(record.staged_path).exists(),
    ) == ([], [record.staged_path], b"y" * 32, False)


def test_rename_failure_preserves_source_and_clears_intent(sandbox_home):
    # Arrange
    parent = sandbox_home / "parent"
    parent.mkdir()
    source = _tree(parent / "work")
    staging = parent / retention.STAGING_DIRNAME
    staging.mkdir()
    parent.chmod(0o555)
    # Act
    failed = False
    try:
        retention.stage_for_retention(source)
    except PermissionError:
        failed = True
    finally:
        parent.chmod(0o755)
    # Assert
    assert (
        failed,
        (source / "top.bin").read_bytes(),
        retention._read_registry(),
        list(staging.iterdir()),
    ) == (True, b"y" * 32, [], [])


def test_corrupt_registry_is_not_silently_replaced(sandbox_home):
    # Arrange
    source = _tree(sandbox_home / "work")
    registry = retention._registry_path()
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b"{invalid")
    # Act
    failed = False
    try:
        retention.stage_for_retention(source)
    except ValueError:
        failed = True
    # Assert
    assert (failed, registry.read_bytes(), (source / "top.bin").read_bytes()) == (
        True,
        b"{invalid",
        b"y" * 32,
    )


def test_processes_cannot_lose_each_others_registry_records(sandbox_home):
    # Arrange
    import subprocess
    import sys
    from pathlib import Path

    roots = [_tree(sandbox_home / f"source-{index}") for index in range(3)]
    source_path = str(Path(retention.__file__).resolve().parents[2])
    code = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from scitex_storage._transfer._retention import stage_for_retention;"
        "stage_for_retention(sys.argv[2])"
    )
    # Act
    processes = [
        subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", code, source_path, str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for root in roots
    ]
    results = [process.communicate(timeout=10) for process in processes]
    entries = retention._read_registry()
    # Assert
    assert (
        [
            (process.returncode, error)
            for process, (_, error) in zip(processes, results)
        ],
        {entry["original_path"] for entry in entries},
    ) == ([(0, b"")] * 3, {str(root) for root in roots})


def test_entry_budget_does_not_begin_large_tree_cleanup(sandbox_home):
    # Arrange
    record = retention.stage_for_retention(
        _tree(sandbox_home / "work"), retention_days=0
    )
    # Act
    report = retention.sweep_restorable(dry_run=False, max_entries=1)
    # Assert
    assert (
        report.removed,
        report.retained,
        retention._read_registry()[0]["staged_path"],
    ) == ([], [record.staged_path], record.staged_path)
