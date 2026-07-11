"""Unit tests for scitex_storage._archive (move-not-delete nas/nas2 tiering).

A fake ``runner`` (matching scitex_ssh's own ``subprocess.run``-shaped
testing seam -- verified against its real call convention:
``runner(cmd, capture_output=True, text=True)`` returning an object with
``.returncode``/``.stdout``/``.stderr``) stands in for real ssh/rsync, so
no network or real SSH config is needed to exercise these code paths.
"""

import os
from dataclasses import dataclass

import pytest

from scitex_storage._archive import (
    ArchiveManifest,
    ArchivePlan,
    RestorePlan,
    apply_archive,
    apply_restore,
    plan_archive,
    plan_restore,
)


@dataclass
class _FakeCompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _FakeRunner:
    """Records every argv it's called with; returns the SAME scripted result
    for every call (mkdir and rsync alike -- use _StagedRunner to make only
    one of them fail)."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return _FakeCompletedProcess(self.returncode, self.stdout, self.stderr)


class _StagedRunner:
    """Scripts a distinct result per call index (1-based) -- e.g. mkdir
    (call 1) succeeds, rsync (call 2) fails. Any call beyond the scripted
    stages reuses the last stage's result."""

    def __init__(self, *stages):
        self.stages = stages
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        stage = self.stages[min(len(self.calls), len(self.stages)) - 1]
        return _FakeCompletedProcess(*stage)


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def sandbox_home(tmp_path):
    """Real temp HOME so manifest writes never touch the real ~/.scitex."""
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


# --- plan_archive -------------------------------------------------------------


def test_plan_archive_raises_for_unknown_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    # Assert
    with pytest.raises(ValueError):
        plan_archive(source, "not-a-real-destination")


def test_plan_archive_raises_for_missing_source(tmp_path, sandbox_home):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_archive(missing, "nas")


def test_plan_archive_raises_for_non_directory_source(tmp_path, sandbox_home):
    # Arrange
    a_file = _touch(tmp_path / "a.bin")
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        plan_archive(a_file, "nas")


def test_plan_archive_computes_size_bytes(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin", size=10)
    _touch(source / "b.bin", size=20)
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.size_bytes == 30


def test_plan_archive_computes_file_count(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    _touch(source / "b.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.file_count == 2


def test_plan_archive_default_remote_path_mirrors_source(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.remote_path == f"~/scitex-storage-archive{source}"


def test_plan_archive_explicit_remote_path_overrides_default(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas", remote_path="~/custom/target")
    # Assert
    assert plan.remote_path == "~/custom/target"


def test_plan_archive_refuses_an_unsafe_remote_path(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    # Assert
    with pytest.raises(ValueError):
        plan_archive(source, "nas", remote_path="/")


def test_plan_archive_does_not_touch_the_source(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    # Act
    plan_archive(source, "nas")
    # Assert
    assert f.exists()


def test_plan_archive_returns_an_archiveplan_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert isinstance(plan, ArchivePlan)


def test_plan_archive_manifest_path_is_deterministic(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan_a = plan_archive(source, "nas")
    plan_b = plan_archive(source, "nas")
    # Assert
    assert plan_a.manifest_path == plan_b.manifest_path


# --- apply_archive --------------------------------------------------------------


def test_apply_archive_removes_the_source_on_success(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert not source.exists()


def test_apply_archive_writes_a_manifest_file_on_success(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert plan.manifest_path.exists()


def test_apply_archive_manifest_records_the_source_path(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert manifest.source == str(source)


def test_apply_archive_creates_the_remote_parent_directory_first(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert
    assert "mkdir -p" in runner.calls[0][-1]


def test_apply_archive_mkdir_runs_before_the_rsync_call(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert
    assert len(runner.calls) == 2


# A uniform-failure runner fails BOTH the mkdir and the rsync call, so it
# exercises the mkdir failure path specifically (mkdir is call 1, and the
# code raises before ever reaching the rsync call).


def test_apply_archive_leaves_source_untouched_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    try:
        apply_archive(plan, runner=_FakeRunner(returncode=1, stderr="mkdir error"))
    except RuntimeError:
        pass
    # Assert
    assert f.exists()


def test_apply_archive_raises_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_archive(plan, runner=_FakeRunner(returncode=1, stderr="mkdir error"))


def test_apply_archive_does_not_write_manifest_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    try:
        apply_archive(plan, runner=_FakeRunner(returncode=1))
    except RuntimeError:
        pass
    # Assert
    assert not plan.manifest_path.exists()


# A staged runner lets mkdir succeed so the rsync call specifically fails.


def test_apply_archive_leaves_source_untouched_on_sync_failure_after_mkdir_ok(
    tmp_path, sandbox_home
):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _StagedRunner((0, "", ""), (1, "", "rsync error"))
    # Act
    try:
        apply_archive(plan, runner=runner)
    except RuntimeError:
        pass
    # Assert
    assert f.exists()


def test_apply_archive_raises_on_sync_failure_after_mkdir_ok(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _StagedRunner((0, "", ""), (1, "", "rsync error"))
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_archive(plan, runner=runner)


def test_apply_archive_checksum_true_adds_the_rsync_flag(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, checksum=True, runner=runner)
    # Assert -- calls[-1] is the rsync argv (mkdir runs first, at calls[0])
    assert "--checksum" in runner.calls[-1]


def test_apply_archive_checksum_false_omits_the_rsync_flag(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, checksum=False, runner=runner)
    # Assert
    assert "--checksum" not in runner.calls[-1]


def test_apply_archive_passes_exclude_patterns_through(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, exclude=("*.tmp",), runner=runner)
    # Assert
    assert "--exclude=*.tmp" in runner.calls[-1]


def test_apply_archive_returns_an_archivemanifest_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert isinstance(manifest, ArchiveManifest)


# --- plan_restore -------------------------------------------------------------


def test_plan_restore_raises_when_no_manifest_exists(tmp_path, sandbox_home):
    # Arrange
    never_archived = tmp_path / "never-archived"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_restore(never_archived)


def test_plan_restore_loads_a_previously_written_manifest(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    archive_plan = plan_archive(source, "nas")
    apply_archive(archive_plan, runner=_FakeRunner(returncode=0))
    # Act
    restore_plan = plan_restore(source)
    # Assert
    assert restore_plan.manifest.source == str(source)


def test_plan_restore_returns_a_restoreplan_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    archive_plan = plan_archive(source, "nas")
    apply_archive(archive_plan, runner=_FakeRunner(returncode=0))
    # Act
    restore_plan = plan_restore(source)
    # Assert
    assert isinstance(restore_plan, RestorePlan)


# --- apply_restore -------------------------------------------------------------


def _restore_plan(source, destination="nas", remote_path="~/archive/x"):
    manifest = ArchiveManifest(
        source=str(source),
        destination=destination,
        remote_path=remote_path,
        size_bytes=10,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
    )
    return RestorePlan(manifest=manifest, manifest_path=source.parent / "unused.json")


def test_apply_restore_returns_the_source_path_on_success(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    # Act
    result = apply_restore(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert result == source


def test_apply_restore_raises_on_pull_failure(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_restore(plan, runner=_FakeRunner(returncode=1, stderr="rsync error"))


def test_apply_restore_without_delete_remote_makes_only_one_call(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=False, runner=runner)
    # Assert
    assert len(runner.calls) == 1


def test_apply_restore_with_delete_remote_makes_a_second_call(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=True, runner=runner)
    # Assert
    assert len(runner.calls) == 2


def test_apply_restore_delete_remote_command_targets_the_remote_path(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="~/archive/needle")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=True, runner=runner)
    # Assert
    assert "needle" in runner.calls[1][-1]


def test_apply_restore_raises_if_remote_delete_fails(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)

    class _TwoStageRunner:
        def __init__(self):
            self.n = 0

        def __call__(self, cmd, **kwargs):
            self.n += 1
            # First call (sync_dir pull) succeeds; second (exec_remote rm) fails.
            return _FakeCompletedProcess(0 if self.n == 1 else 1, "", "rm failed")

    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_restore(plan, delete_remote=True, runner=_TwoStageRunner())


def test_apply_restore_refuses_to_delete_an_unsafe_remote_path(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="/")
    # Act
    # Assert
    with pytest.raises(ValueError):
        apply_restore(plan, delete_remote=True, runner=_FakeRunner(returncode=0))


# EOF
