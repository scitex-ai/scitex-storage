"""Unit tests for the ``scitex-storage scan`` CLI command.

``scan`` shells out to `fd` (see _scan.py) -- tests that actually invoke
the command end-to-end are gated on the real binary being on PATH via
`requires_fd`, mirroring test__scan.py. Tests that never reach that code
path (bad-path validation, the "fd absent" error message) are not gated:
they exercise pure Click/argument-parsing logic or explicitly simulate
the binary's absence.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main

_HAVE_FD = bool(shutil.which("fd") or shutil.which("fdfind"))
requires_fd = pytest.mark.skipif(
    not _HAVE_FD, reason="requires the `fd` (fd-find) binary on PATH"
)


def _touch(path, size=1, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def isolated_path_bin_dir(tmp_path):
    """Replace PATH with a fresh, empty directory for the test's duration.

    A real env-var mutation (not a mock, STX-NM002) -- the code under test
    still calls the real `shutil.which`.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    original_path = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    yield bin_dir
    os.environ["PATH"] = original_path


@pytest.fixture
def home_with_scitex():
    """Real temp HOME containing a populated ~/.scitex (and no ~/proj).

    Sets ``$HOME`` for the duration of the test and restores it on
    teardown — the sanctioned env-var pattern (no ``monkeypatch``).
    """
    prev = os.environ.get("HOME")
    with tempfile.TemporaryDirectory() as td:
        os.environ["HOME"] = td
        state = Path(td) / ".scitex" / "state"
        state.mkdir(parents=True)
        (state / "x.bin").write_bytes(b"\0" * 10)
        try:
            yield Path(td)
        finally:
            if prev is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev


@requires_fd
def test_cli_scan_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


@requires_fd
def test_cli_scan_prints_root(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert str(tmp_path.resolve()) in result.output


@requires_fd
def test_cli_scan_json_flag_emits_valid_json(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--json"])
    # Assert
    assert len(json.loads(result.output)["roots"][0]["children"]) == 1


@requires_fd
def test_cli_scan_sort_files_exits_zero(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path), "--sort", "files"])
    # Assert
    assert result.exit_code == 0


def test_cli_scan_reports_clean_error_when_fd_absent(tmp_path, isolated_path_bin_dir):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", str(tmp_path)])
    # Assert
    assert "cargo install fd-find" in result.output


def test_cli_scan_rejects_missing_path():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan", "/no/such/path/at/all"])
    # Assert
    assert result.exit_code != 0


@requires_fd
def test_cli_scan_no_args_scans_default_roots(home_with_scitex):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["scan"])
    # Assert
    assert result.exit_code == 0


# EOF
