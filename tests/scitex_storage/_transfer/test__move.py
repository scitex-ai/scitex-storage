"""Real rsync, filesystem and process-group tests for caller-authorized moves."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from scitex_storage._transfer._content_verify import DigestCancelled
from scitex_storage._transfer._move import (
    MoveCancelled,
    MoveRecoveryRequired,
    _run_process_group,
    apply_move,
    get_move_status,
    plan_move,
)


@pytest.fixture
def move_tree(tmp_path):
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "sub/paper.bin").write_bytes(b"verified bytes")
    state = tmp_path / "user-home/.scitex/scitex-storage/runtime/move-manifests"
    return source, tmp_path / "destination", state


def test_move_planning_does_not_write_state_or_copy_data(move_tree):
    # Arrange
    source, destination, state = move_tree
    # Act
    plan = plan_move(source, destination, state_dir=state)
    # Assert
    assert (plan.size_bytes, source.exists(), destination.exists(), state.exists()) == (
        14,
        True,
        False,
        False,
    )


def test_real_directory_move_publishes_verified_copy_and_stages_original(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    # Act
    receipt = apply_move(plan)
    registry = json.loads((state.parent / "restorable-registry.json").read_text())
    # Assert
    assert (
        receipt.phase,
        receipt.verification_method,
        (destination / "sub/paper.bin").read_bytes(),
        (Path(receipt.staged_path) / "sub/paper.bin").read_bytes(),
        registry[0]["delete_after"] - registry[0]["staged_at"],
        source.exists(),
    ) == ("retired", "content", b"verified bytes", b"verified bytes", 7 * 86400, False)


def test_real_file_move_preserves_file_payload(move_tree):
    # Arrange
    source, destination, state = move_tree
    original = source / "sub/paper.bin"
    plan = plan_move(original, destination, state_dir=state)
    # Act
    receipt = apply_move(plan)
    # Assert
    assert (
        receipt.phase,
        destination.read_bytes(),
        Path(receipt.staged_path).read_bytes(),
    ) == ("retired", b"verified bytes", b"verified bytes")


def test_existing_destination_cannot_be_planned(move_tree):
    # Arrange
    source, destination, state = move_tree
    destination.write_bytes(b"keep")
    # Act
    raised = pytest.raises(FileExistsError)
    # Assert
    with raised:
        plan_move(source, destination, state_dir=state)


def test_destination_created_after_plan_is_never_overwritten(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    destination.write_bytes(b"new occupant")
    # Act
    refused = False
    try:
        apply_move(plan)
    except MoveRecoveryRequired:
        refused = True
    # Assert
    assert (
        refused,
        destination.read_bytes(),
        (source / "sub/paper.bin").read_bytes(),
    ) == (True, b"new occupant", b"verified bytes")


def test_move_destination_cannot_be_inside_source(move_tree):
    # Arrange
    source, _, state = move_tree
    # Act
    raised = pytest.raises(ValueError, match="contain")
    # Assert
    with raised:
        plan_move(source, source / "nested", state_dir=state)


def test_cancelled_move_never_starts_data_copy(move_tree):
    # Arrange
    source, destination, state = move_tree
    event = threading.Event()
    event.set()
    plan = plan_move(source, destination, state_dir=state)
    # Act
    phase = ""
    try:
        apply_move(plan, cancel_event=event)
    except MoveCancelled as exc:
        phase = exc.manifest.phase
    # Assert
    assert (phase, source.exists(), destination.exists()) == ("cancelled", True, False)


def test_expired_monotonic_deadline_prevents_data_copy(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    # Act
    phase = ""
    try:
        apply_move(plan, deadline=time.monotonic() - 1)
    except MoveCancelled as exc:
        phase = exc.manifest.phase
    # Assert
    assert (phase, source.exists(), destination.exists()) == ("cancelled", True, False)


def test_completed_transaction_replay_returns_same_historical_receipt(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    first = apply_move(plan)
    before = destination.stat().st_mtime_ns
    # Act
    again = apply_move(plan)
    # Assert
    assert (again, destination.stat().st_mtime_ns) == (first, before)


def test_cancelled_transaction_is_not_blindly_replayed(move_tree):
    # Arrange
    source, destination, state = move_tree
    event = threading.Event()
    event.set()
    plan = plan_move(source, destination, state_dir=state)
    try:
        apply_move(plan, cancel_event=event)
    except MoveCancelled:
        pass
    event.clear()
    # Act
    refused = False
    try:
        apply_move(plan, cancel_event=event)
    except MoveRecoveryRequired:
        refused = True
    # Assert
    assert (refused, source.exists(), destination.exists()) == (True, True, False)


def test_get_move_status_uses_trusted_per_user_state_directory(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    expected = apply_move(plan)
    # Act
    actual = get_move_status(plan.transaction_id, state_dir=state)
    # Assert
    assert (actual, actual.manifest_path) == (
        expected,
        str(state / f"{plan.transaction_id}.json"),
    )


def test_source_size_change_requires_new_plan(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    (source / "new.bin").write_bytes(b"new")
    # Act
    refused = False
    try:
        apply_move(plan)
    except MoveRecoveryRequired:
        refused = True
    # Assert
    assert (refused, (source / "new.bin").read_bytes(), destination.exists()) == (
        True,
        b"new",
        False,
    )


def _wait_for_path(path, timeout=5):
    until = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= until:
            raise TimeoutError("Owned process did not become ready")
        threading.Event().wait(0.01)


def _process_is_running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
    except FileNotFoundError:
        return False
    return state not in {"Z", "X"}


def test_cancellation_terminates_real_process_group_and_child(tmp_path):
    # Arrange
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "owned_parent.py"
    script.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-I','-S','-c',\n"
        "'import time;time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\ntime.sleep(60)\n"
    )
    event = threading.Event()
    failures = []

    def run():
        try:
            _run_process_group(
                [sys.executable, "-I", "-S", str(script), str(pid_file)], event.is_set
            )
        except DigestCancelled as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=run)
    # Act
    worker.start()
    try:
        _wait_for_path(pid_file)
        child = int(pid_file.read_text())
    finally:
        event.set()
        worker.join(timeout=5)
    # Assert
    assert (worker.is_alive(), failures, _process_is_running(child)) == (
        False,
        ["DigestCancelled"],
        False,
    )


def test_live_transaction_lock_returns_pending_without_copying(move_tree):
    # Arrange
    from scitex_storage._transfer._durable import file_lock
    from scitex_storage._transfer._move import MoveInProgress

    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    # Act
    phase = ""
    with file_lock(plan.manifest_path.with_suffix(".lock")):
        try:
            apply_move(plan)
        except MoveInProgress as exc:
            phase = exc.manifest.phase
    # Assert
    assert (phase, source.exists(), destination.exists()) == ("accepted", True, False)


def test_two_real_move_workers_cannot_overwrite_same_destination(tmp_path):
    # Arrange
    import scitex_storage._transfer._move as module

    source_root = str(Path(module.__file__).resolve().parents[2])
    source_a = tmp_path / "first.bin"
    source_b = tmp_path / "second.bin"
    source_a.write_bytes(b"first payload")
    source_b.write_bytes(b"second payload")
    destination = tmp_path / "destination.bin"
    state = tmp_path / "runtime/move-manifests"
    start = tmp_path / "start"
    ready = [tmp_path / "first.ready", tmp_path / "second.ready"]
    code = """import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scitex_storage._transfer._move import plan_move,apply_move,MoveRecoveryRequired
plan=plan_move(sys.argv[2],sys.argv[3],state_dir=sys.argv[4])
Path(sys.argv[5]).write_text('ready')
until=time.monotonic()+10
while not Path(sys.argv[6]).exists():
    if time.monotonic()>until: raise TimeoutError('start barrier')
    time.sleep(0.01)
try:
    apply_move(plan)
except MoveRecoveryRequired:
    sys.exit(2)
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                code,
                source_root,
                str(source),
                str(destination),
                str(state),
                str(marker),
                str(start),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for source, marker in zip((source_a, source_b), ready)
    ]
    # Act
    try:
        for marker in ready:
            _wait_for_path(marker)
        start.write_text("go")
        results = [process.communicate(timeout=10) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    receipts = [json.loads(path.read_text()) for path in state.glob("*.json")]
    winner = next(row for row in receipts if row["phase"] == "retired")
    # Assert
    assert (
        sorted(process.returncode for process in processes),
        sorted(row["phase"] for row in receipts),
        sum(source.exists() for source in (source_a, source_b)),
        destination.read_bytes() == Path(winner["staged_path"]).read_bytes(),
        [error for _, error in results],
    ) == ([0, 2], ["failed", "retired"], 1, True, [b"", b""])


def _expired_cleanup(plan, **budgets):
    from scitex_storage._transfer._retention import _read_registry, sweep_restorable

    registry = plan.manifest_path.parent.parent / "restorable-registry.json"
    entry = _read_registry(registry_path=registry)[0]
    return sweep_restorable(
        dry_run=False, now=entry["delete_after"] + 1, registry_path=registry, **budgets
    )


def test_expired_move_retention_requires_destination_to_still_exist(move_tree):
    # Arrange
    import shutil

    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    receipt = apply_move(plan)
    shutil.rmtree(destination)
    # Act
    report = _expired_cleanup(plan)
    # Assert
    assert (
        report.removed,
        report.retained,
        (Path(receipt.staged_path) / "sub/paper.bin").read_bytes(),
    ) == ([], [receipt.staged_path], b"verified bytes")


def test_same_size_destination_corruption_keeps_original_quarantine(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    receipt = apply_move(plan)
    (destination / "sub/paper.bin").write_bytes(b"damaged bytes!")
    # Act
    report = _expired_cleanup(plan)
    # Assert
    assert (
        report.removed,
        report.retained,
        (Path(receipt.staged_path) / "sub/paper.bin").read_bytes(),
    ) == ([], [receipt.staged_path], b"verified bytes")


def test_healthy_verified_destination_allows_expired_original_cleanup(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    receipt = apply_move(plan)
    # Act
    report = _expired_cleanup(plan)
    # Assert
    assert (
        report.removed,
        Path(receipt.staged_path).exists(),
        (destination / "sub/paper.bin").read_bytes(),
    ) == ([receipt.staged_path], False, b"verified bytes")


def test_missing_move_receipt_cannot_authorize_original_cleanup(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    receipt = apply_move(plan)
    plan.manifest_path.unlink()
    # Act
    report = _expired_cleanup(plan)
    # Assert
    assert (report.removed, report.retained, Path(receipt.staged_path).exists()) == (
        [],
        [receipt.staged_path],
        True,
    )


def test_cleanup_budget_includes_both_original_and_destination_hashes(move_tree):
    # Arrange
    source, destination, state = move_tree
    plan = plan_move(source, destination, state_dir=state)
    receipt = apply_move(plan)
    # Act
    report = _expired_cleanup(plan, max_bytes=plan.size_bytes)
    # Assert
    assert (report.removed, report.retained, Path(receipt.staged_path).exists()) == (
        [],
        [receipt.staged_path],
        True,
    )


def test_source_parent_symlink_is_not_silently_rebound(move_tree):
    # Arrange
    source, destination, state = move_tree
    alias = source.parent / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    # Act
    raised = pytest.raises(ValueError, match="canonical")
    # Assert
    with raised:
        plan_move(alias / "sub/paper.bin", destination, state_dir=state)


def test_destination_parent_symlink_is_not_silently_rebound(move_tree):
    # Arrange
    source, destination, state = move_tree
    parent = source.parent / "actual-target"
    parent.mkdir()
    alias = source.parent / "target-alias"
    alias.symlink_to(parent, target_is_directory=True)
    # Act
    raised = pytest.raises(ValueError, match="canonical")
    # Assert
    with raised:
        plan_move(source, alias / destination.name, state_dir=state)


@pytest.mark.parametrize(
    "overlap",
    ["state-in-source", "source-in-state", "state-in-target", "target-in-state"],
)
def test_transaction_state_cannot_move_with_the_data(tmp_path, overlap):
    # Arrange
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.txt").write_text("preserve")
    target = tmp_path / "target"
    states = {
        "state-in-source": source / "runtime",
        "source-in-state": tmp_path,
        "state-in-target": target / "runtime",
        "target-in-state": target,
    }
    state = states[overlap]
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    # Act
    try:
        plan_move(source, target, state_dir=state)
        verdict = "allowed"
    except ValueError:
        verdict = "refused"
    # Assert
    assert (verdict, sorted(str(p) for p in tmp_path.rglob("*"))) == ("refused", before)
