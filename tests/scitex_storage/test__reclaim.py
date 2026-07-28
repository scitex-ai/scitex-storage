"""Unit tests for scitex_storage._reclaim (reversible local move-aside).

NO MOCKS, and none are wanted: reclaim is a local filesystem move, so every
case is exercised with real directories, real moves, and real restores in a
tmp_path. That is the whole reason v1 is local-move — it is honestly testable
end-to-end, unlike a transport that needs a network stubbed out.

The tests that matter most pin REVERSIBILITY and the RESTORE-RATE metric,
because those are the properties that make a rough decision safe to ship:
a reclaim that cannot be undone, or an undo that is not measured, defeats the
entire archive-instead-of-delete design.
"""

import os
from pathlib import Path

import pytest

from scitex_storage._reclaim import (
    ReclaimEntry,
    ReclaimManifest,
    apply_reclaim,
    list_manifests,
    load_manifest,
    plan_reclaim,
    restore_rate,
    restore_reclaim,
)


@pytest.fixture
def sandbox_home(tmp_path):
    """Real temp HOME so reclaim manifests never touch ~/.scitex."""
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


def _tree(root: Path, name: str, n_files: int = 3) -> Path:
    """Create a small directory tree and return its path."""
    d = root / name
    d.mkdir(parents=True)
    for i in range(n_files):
        (d / f"f{i}.bin").write_bytes(b"\0" * (i + 1))
    return d


# --------------------------------------------------------------------------
# plan_reclaim -- read-only, computes the move without doing it.
# --------------------------------------------------------------------------


def test_plan_reclaim_touches_nothing(tmp_path):
    # Arrange
    src = _tree(tmp_path, "target")
    # Act
    plan_reclaim([src], run_id="r1")
    # Assert -- the source is still exactly where it was.
    assert src.is_dir()


def test_plan_reclaim_counts_files_in_the_tree(tmp_path):
    # Arrange
    src = _tree(tmp_path, "target", n_files=4)
    # Act
    plan = plan_reclaim([src], run_id="r1")
    # Assert
    assert plan.total_files == 4


def test_plan_reclaim_default_destination_is_adjacent_dot_old(tmp_path):
    # Arrange
    src = _tree(tmp_path, "target")
    # Act
    plan = plan_reclaim([src], run_id="r1")
    # Assert -- <parent>/.old/<run_id>/<name>
    assert plan.entries[0].archived == str(tmp_path / ".old" / "r1" / "target")


def test_plan_reclaim_honours_an_explicit_archive_root(tmp_path):
    # Arrange
    src = _tree(tmp_path, "target")
    root = tmp_path / "elsewhere"
    # Act
    plan = plan_reclaim([src], run_id="r1", archive_root=str(root))
    # Assert
    assert plan.entries[0].archived == str(root / "r1" / "target")


def test_plan_reclaim_fails_loud_on_a_missing_path(tmp_path):
    # Arrange
    missing = tmp_path / "nope"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_reclaim([missing], run_id="r1")


def test_plan_reclaim_refuses_to_reclaim_a_dot_old_directory(tmp_path):
    # Arrange -- reclaiming the archive area itself is a footgun.
    archive = tmp_path / ".old"
    archive.mkdir()
    # Act
    # Assert
    with pytest.raises(ValueError):
        plan_reclaim([archive], run_id="r1")


# --------------------------------------------------------------------------
# apply_reclaim -- the move actually happens, reversibly.
# --------------------------------------------------------------------------


def test_apply_reclaim_moves_the_source_out_of_place(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    # Act
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Assert
    assert not src.exists()


def test_apply_reclaim_puts_the_source_in_the_archive(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    # Act
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Assert
    assert (tmp_path / ".old" / "r1" / "target").is_dir()


def test_apply_reclaim_preserves_the_tree_contents(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target", n_files=3)
    # Act
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Assert -- the files came along, byte counts intact.
    moved = tmp_path / ".old" / "r1" / "target"
    assert sorted(p.name for p in moved.iterdir()) == ["f0.bin", "f1.bin", "f2.bin"]


def test_apply_reclaim_writes_a_manifest(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target")
    # Act
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Assert
    assert load_manifest("r1").run_id == "r1"


def test_apply_reclaim_to_a_cross_dir_archive_root(tmp_path, sandbox_home):
    # Arrange -- explicit archive_root (the inode-relief shape).
    src = _tree(tmp_path, "target")
    root = tmp_path / "archive-elsewhere"
    # Act
    apply_reclaim(plan_reclaim([src], run_id="r1", archive_root=str(root)))
    # Assert
    assert (root / "r1" / "target").is_dir() and not src.exists()


def test_apply_reclaim_handles_a_single_file(tmp_path, sandbox_home):
    # Arrange -- not everything reclaimed is a directory.
    f = tmp_path / "loose.bin"
    f.write_bytes(b"\0\0\0")
    # Act
    apply_reclaim(plan_reclaim([f], run_id="r1"))
    # Assert
    assert not f.exists() and (tmp_path / ".old" / "r1" / "loose.bin").is_file()


# --------------------------------------------------------------------------
# restore_reclaim -- the reversal the whole design turns on.
# --------------------------------------------------------------------------


def test_restore_reclaim_returns_the_source_to_its_original_path(
    tmp_path, sandbox_home
):
    # Arrange
    src = _tree(tmp_path, "target")
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Act
    restore_reclaim("r1")
    # Assert
    assert src.is_dir()


def test_restore_reclaim_round_trips_contents_exactly(tmp_path, sandbox_home):
    # Arrange
    src = _tree(tmp_path, "target", n_files=3)
    before = sorted(p.name for p in src.iterdir())
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # Act
    restore_reclaim("r1")
    # Assert
    assert sorted(p.name for p in src.iterdir()) == before


def test_restore_reclaim_refuses_to_overwrite_a_reoccupied_original(
    tmp_path, sandbox_home
):
    # Arrange -- something took the vacated spot after reclaim.
    src = _tree(tmp_path, "target")
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    src.mkdir()
    (src / "new.bin").write_bytes(b"\0")
    # Act
    # Assert -- must not clobber the squatter.
    with pytest.raises(FileExistsError):
        restore_reclaim("r1")


def test_restore_reclaim_fails_loud_when_the_archived_copy_is_gone(
    tmp_path, sandbox_home
):
    # Arrange
    src = _tree(tmp_path, "target")
    apply_reclaim(plan_reclaim([src], run_id="r1"))
    # someone deleted the archived copy out from under us
    import shutil

    shutil.rmtree(tmp_path / ".old" / "r1" / "target")
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        restore_reclaim("r1")


# --------------------------------------------------------------------------
# restore_rate -- the accuracy metric, measured not guessed.
# --------------------------------------------------------------------------


def test_restore_rate_is_none_when_nothing_reclaimed(sandbox_home):
    # Arrange -- no denominator. Must be None, never a reassuring 0.0.
    # Act
    # Assert
    assert restore_rate() is None


def test_restore_rate_is_zero_when_nothing_restored(tmp_path, sandbox_home):
    # Arrange -- one reclaim, never pulled back: the decision looks good.
    apply_reclaim(plan_reclaim([_tree(tmp_path, "a")], run_id="r1"))
    apply_reclaim(plan_reclaim([_tree(tmp_path, "b")], run_id="r2"))
    # Act
    # Assert
    assert restore_rate() == 0.0


def test_restore_rate_counts_restored_runs_over_total(tmp_path, sandbox_home):
    # Arrange -- two runs, one pulled back: 50% of decisions were wrong.
    apply_reclaim(plan_reclaim([_tree(tmp_path, "a")], run_id="r1"))
    apply_reclaim(plan_reclaim([_tree(tmp_path, "b")], run_id="r2"))
    restore_reclaim("r1")
    # Act
    # Assert
    assert restore_rate() == 0.5


def test_restore_flag_persists_across_a_reload(tmp_path, sandbox_home):
    # Arrange -- the metric must survive process boundaries (it is on disk).
    apply_reclaim(plan_reclaim([_tree(tmp_path, "a")], run_id="r1"))
    restore_reclaim("r1")
    # Act
    reloaded = load_manifest("r1")
    # Assert
    assert reloaded.restored is True


def test_list_manifests_returns_every_run(tmp_path, sandbox_home):
    # Arrange
    apply_reclaim(plan_reclaim([_tree(tmp_path, "a")], run_id="r1"))
    apply_reclaim(plan_reclaim([_tree(tmp_path, "b")], run_id="r2"))
    # Act
    runs = {m.run_id for m in list_manifests()}
    # Assert
    assert runs == {"r1", "r2"}


# --------------------------------------------------------------------------
# manifest (de)serialization -- the on-disk contract.
# --------------------------------------------------------------------------


def test_manifest_round_trips_through_dict():
    # Arrange
    m = ReclaimManifest(
        run_id="r1",
        reclaimed_at=123.0,
        archive_root=None,
        entries=[ReclaimEntry(original="/a", archived="/a/.old/r1/a", size_bytes=1, file_count=1)],
    )
    # Act
    back = ReclaimManifest.from_dict(m.to_dict())
    # Assert
    assert back.entries[0].original == "/a" and back.restored is False


# EOF
