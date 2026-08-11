"""Unit tests for the ``scitex-storage archive`` / ``restore`` CLI commands.

--yes isn't exercised here (it would shell out to real ssh/rsync with no
fake-runner injection point at the CLI layer) -- that coverage lives in
tests/scitex_storage/test__archive.py's fake-runner tests instead. These
cover dry-run + validation only.
"""

import json
import os

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture(autouse=True)
def stub_rsync_on_path(tmp_path):
    """Put a real (never-invoked) `rsync` executable on PATH for every test here.

    `archive`/`restore` now check for the local rsync BEFORE planning, so the
    dry-run cannot promise a run whose transport could not start. Without
    this fixture these tests would silently depend on the HOST having rsync
    -- passing on a CI image that ships it and failing in the container that
    does not, which is the environment-dependence the guard exists to expose
    rather than acquire. Here they depend on nothing.

    Not a mock: a real file that the real `shutil.which` really finds. The
    dry-run only LOCATES rsync, never runs it, so the stub's (absent)
    behaviour is never consulted -- same spirit as `_scan.py`'s fd tests,
    which symlink a real binary onto an isolated PATH.

    The one test that wants rsync ABSENT isolates PATH itself and overrides
    this (autouse fixtures set up first, so its replacement wins).
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "rsync"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    prev = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{prev}"
    try:
        yield bin_dir
    finally:
        os.environ["PATH"] = prev


@pytest.fixture
def sandbox_home(tmp_path):
    """Real temp HOME so archive manifest writes never touch ~/.scitex."""
    home = tmp_path / "cli-home"
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


def test_cli_archive_exits_zero(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert
    assert result.exit_code == 0


def test_cli_archive_requires_to_flag():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "/tmp"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_rejects_unknown_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "bogus"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_rejects_missing_source():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "/no/such/path", "--to", "nas"])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_defaults_to_dry_run(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert
    assert f.exists()


def test_cli_archive_explicit_dry_run_flag_exits_zero(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--dry-run"])
    # Assert
    assert result.exit_code == 0


def test_cli_archive_yes_with_dry_run_does_not_mutate(tmp_path, sandbox_home):
    # Arrange -- --dry-run wins over --yes, so this never touches the network
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    runner.invoke(main, ["archive", str(source), "--to", "nas", "--yes", "--dry-run"])
    # Assert
    assert f.exists()


def test_cli_archive_json_reports_the_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--json"])
    # Assert
    assert json.loads(result.output)["destination"] == "nas"


def test_cli_archive_json_reports_applied_false_on_dry_run(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas", "--json"])
    # Assert
    assert json.loads(result.output)["applied"] is False


def test_cli_restore_rejects_when_never_archived(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "never-archived"
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["restore", str(source)])
    # Assert
    assert result.exit_code != 0


# =============================================================================
# rsync dependency -- the DRY-RUN must not promise a run it cannot start.
#
# These pin the reason the guard sits before `plan_archive` rather than at
# `--yes`: the dry-run is this command's DEFAULT, so a user on a box with no
# rsync would otherwise be told "WOULD ARCHIVE <n> GB" and only discover the
# truth at the moment they committed to it. Found 2026-07-17 -- `archive` did
# not run in the container scitex-storage ships to, and rsync was in neither
# the docs nor the declared system-deps.
# =============================================================================


@pytest.fixture
def isolated_path_bin_dir(tmp_path):
    """Replace PATH with a fresh, empty directory (no rsync) for one test."""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    prev = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    try:
        yield bin_dir
    finally:
        os.environ["PATH"] = prev


def test_cli_archive_dry_run_fails_when_rsync_is_absent(
    tmp_path, sandbox_home, isolated_path_bin_dir
):
    # Arrange -- the default dry-run, on a box with no transport.
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert -- refuses rather than reporting a plan it could never execute.
    assert result.exit_code != 0


def test_cli_archive_missing_rsync_names_the_binary_and_how_to_get_it(
    tmp_path, sandbox_home, isolated_path_bin_dir
):
    # Arrange -- a raw traceback from inside a SIBLING package (scitex-ssh),
    # naming a binary this package's docs never mentioned, is what this
    # replaces. The user must not have to reverse-engineer
    # scitex-storage -> scitex-ssh -> rsync.
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", str(source), "--to", "nas"])
    # Assert
    assert "apt install rsync" in result.output


def test_cli_restore_dry_run_fails_when_rsync_is_absent(
    tmp_path, sandbox_home, isolated_path_bin_dir
):
    # Arrange -- restore's dry-run carries the same promise as archive's.
    source = tmp_path / "source"
    _touch(source / "a.bin")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["restore", str(source)])
    # Assert
    assert result.exit_code != 0


def test_cli_archive_exposes_the_content_gate_flag():
    # Arrange -- REACHABILITY is the whole point of this test. apply_archive
    # has carried `verify_content_too` since the content gate shipped, but the
    # `archive` command never passed it and offered no flag, so the only
    # caller that performs the irreversible delete could not turn the gate on.
    # A safety feature the dangerous path cannot reach is not shipped.
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "--help"])
    # Assert
    assert "--verify-content" in result.output


def test_cli_archive_content_gate_defaults_to_off():
    # Arrange -- opt-in on purpose: rsync --checksum already reads every byte,
    # so this is a second opinion from a different instrument and costs a full
    # re-read of both trees. Pinning the default so it cannot drift silently
    # into every archive, which would be a large cost nobody asked for.
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["archive", "--help"])
    # Assert
    assert "--no-verify-content" in result.output


# EOF
