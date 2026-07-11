"""Unit tests for the ``scitex-storage find-duplicates`` CLI command.

``find-duplicates`` shells out to `fclones` (see _duplicates.py) -- tests
that actually invoke the command end-to-end are gated on the real binary
being on PATH via `requires_fclones`, mirroring test__duplicates.py. Tests
that never reach that code path (missing-argument validation, the
"fclones absent" error message) are not gated: they exercise pure
Click/argument-parsing logic or explicitly simulate the binary's absence.
"""

import json
import os
import shutil

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main

_HAVE_FCLONES = bool(shutil.which("fclones"))
requires_fclones = pytest.mark.skipif(
    not _HAVE_FCLONES, reason="requires the `fclones` binary on PATH"
)


def _touch(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
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


@requires_fclones
def test_cli_find_duplicates_exits_zero(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 20)
    (tmp_path / "b.bin").write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path)])
    # Assert
    assert result.exit_code == 0


@requires_fclones
def test_cli_find_duplicates_reports_the_pair(tmp_path):
    # Arrange
    a = _touch(tmp_path / "a.bin", 20)
    b = _touch(tmp_path / "b.bin", 20)
    a.write_bytes(b"x" * 20)
    b.write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path)])
    # Assert
    assert str(a) in result.output


@requires_fclones
def test_cli_find_duplicates_json_flag_emits_valid_json(tmp_path):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x" * 20)
    (tmp_path / "b.bin").write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path), "--json"])
    # Assert
    assert json.loads(result.output)["group_count"] == 1


def test_cli_find_duplicates_reports_clean_error_when_fclones_absent(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    (tmp_path / "a.bin").write_bytes(b"x")
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path)])
    # Assert
    assert "cargo install fclones" in result.output


def test_cli_find_duplicates_requires_at_least_one_path():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates"])
    # Assert
    assert result.exit_code != 0


# EOF
