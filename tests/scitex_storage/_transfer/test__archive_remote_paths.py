"""Archive and backup must not guess a remote working directory."""

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main
from scitex_storage._transfer._archive import ArchivePlan, apply_archive, plan_archive


def refusal(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    return ""


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    (path / "payload.bin").write_bytes(b"keep the source")
    return path


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "remote", ["archive/data", "./archive", "../archive", "~other/data", " /archive"]
)
def test_plan_rejects_ambiguous_remote_paths(source, remote):
    # Arrange
    before = (source / "payload.bin").read_bytes()
    # Act
    message = refusal(lambda: plan_archive(source, "scitex-nas-02", remote_path=remote))
    # Assert
    assert (
        "absolute remote path" in message,
        "~/" in message,
        (source / "payload.bin").read_bytes(),
    ) == (True, True, before)


@pytest.mark.parametrize("command", ["archive", "backup"])
@pytest.mark.parametrize(
    "remote", ["archive/data", "./archive", "../archive", "~other/data", " /archive"]
)
def test_cli_rejects_ambiguous_paths_before_transport(source, command, remote):
    # Arrange -- an empty PATH cannot start rsync or SSH even on old source.
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [command, str(source), "--to", "scitex-nas-02", "--remote-path", remote],
        env={"PATH": ""},
    )
    # Assert -- parameter validation, before binary checks or remote probing.
    assert (
        result.exit_code,
        "--remote-path" in result.output,
        "~/" in result.output,
        (source / "payload.bin").read_bytes(),
    ) == (2, True, True, b"keep the source")


@pytest.mark.requires_fd
@pytest.mark.parametrize(
    "remote", ["/archive/data", "~/archive/data", "/a dir/data", "~/a dir/data"]
)
def test_plan_preserves_explicit_remote_locations(source, remote):
    # Arrange
    before = sorted(source.iterdir())
    # Act
    plan = plan_archive(source, "scitex-nas-02", remote_path=remote)
    # Assert
    assert (plan.remote_path, sorted(source.iterdir())) == (remote, before)


@pytest.mark.requires_fd
def test_default_remote_location_still_uses_remote_home(source):
    # Arrange
    expected = f"~/scitex-storage-archive{source.resolve()}"
    # Act
    plan = plan_archive(source, "scitex-nas-02")
    # Assert
    assert plan.remote_path == expected


def test_apply_rejects_legacy_relative_plan_before_reading_source(tmp_path):
    # Arrange -- a caller-created plan must be refused before baseline or SSH.
    manifest = tmp_path / "manifest.json"
    plan = ArchivePlan(
        tmp_path / "missing", "scitex-nas-02", "relative/archive", 0, 0, manifest
    )
    # Act
    message = refusal(lambda: apply_archive(plan))
    # Assert
    assert (
        "absolute remote path" in message,
        "~/" in message,
        manifest.exists(),
        plan.source.exists(),
    ) == (True, True, False, False)
