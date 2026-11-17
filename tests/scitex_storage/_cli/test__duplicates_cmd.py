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
import shlex
import shutil
import subprocess
import sys

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


def test_cli_find_duplicates_sizes_only_exits_zero_without_fclones(
    tmp_path, isolated_path_bin_dir
):
    # Arrange: PATH emptied -- sizes-only must not reach `fclones` at all.
    a = _touch(tmp_path / "a.bin", 20)
    b = _touch(tmp_path / "b.bin", 20)
    a.write_bytes(b"x" * 20)
    b.write_bytes(b"x" * 20)
    (tmp_path / "unique.bin").write_bytes(b"y" * 21)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path), "--sizes-only"])
    # Assert
    assert result.exit_code == 0


def test_cli_find_duplicates_sizes_only_reports_bytes_at_stake(
    tmp_path, isolated_path_bin_dir
):
    # Arrange: PATH emptied -- sizes-only must not reach `fclones` at all.
    a = _touch(tmp_path / "a.bin", 20)
    b = _touch(tmp_path / "b.bin", 20)
    a.write_bytes(b"x" * 20)
    b.write_bytes(b"x" * 20)
    (tmp_path / "unique.bin").write_bytes(b"y" * 21)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path), "--sizes-only"])
    # Assert
    assert "at stake" in result.output


def test_cli_find_duplicates_sizes_only_lists_duplicate_paths(
    tmp_path, isolated_path_bin_dir
):
    # Arrange: PATH emptied -- sizes-only must not reach `fclones` at all.
    a = _touch(tmp_path / "a.bin", 20)
    b = _touch(tmp_path / "b.bin", 20)
    a.write_bytes(b"x" * 20)
    b.write_bytes(b"x" * 20)
    (tmp_path / "unique.bin").write_bytes(b"y" * 21)
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(tmp_path), "--sizes-only"])
    # Assert
    assert str(a) in result.output


def test_cli_find_duplicates_sizes_only_json_exits_zero(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    _touch(tmp_path / "a.bin", 20).write_bytes(b"x" * 20)
    _touch(tmp_path / "b.bin", 20).write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["find-duplicates", str(tmp_path), "--sizes-only", "--json"]
    )
    # Assert
    assert result.exit_code == 0


def test_cli_find_duplicates_sizes_only_json_reports_shared_size_count(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    _touch(tmp_path / "a.bin", 20).write_bytes(b"x" * 20)
    _touch(tmp_path / "b.bin", 20).write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["find-duplicates", str(tmp_path), "--sizes-only", "--json"]
    )
    # Assert
    assert json.loads(result.output)["shared_sizes"] == 1


def test_cli_find_duplicates_sizes_only_json_reports_bytes_at_stake(
    tmp_path, isolated_path_bin_dir
):
    # Arrange
    _touch(tmp_path / "a.bin", 20).write_bytes(b"x" * 20)
    _touch(tmp_path / "b.bin", 20).write_bytes(b"x" * 20)
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main, ["find-duplicates", str(tmp_path), "--sizes-only", "--json"]
    )
    # Assert
    assert json.loads(result.output)["bytes_at_stake"] == 20


# EOF


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        "exit",
        "vanished",
        "malformed",
        "missing-groups",
        "root-array",
        "groups-null",
        "groups-object",
        "row-null",
        "missing-files",
        "files-string",
        "nonpath",
        "empty-path",
        "nul-path",
    ],
)
def test_duplicate_failure_is_readable_without_a_success_payload(
    tmp_path, failure, as_json
):
    # Arrange: a real executable controls only the external engine protocol.
    source = tmp_path / "source"
    source.mkdir()
    left, right = source / "left.bin", source / "right.bin"
    left.write_bytes(b"owned duplicate fixture")
    right.write_bytes(left.read_bytes())
    binaries = tmp_path / "bin"
    binaries.mkdir()
    payload = json.dumps({"groups": [{"files": [str(left), str(right)]}]})
    invalid_reports = {
        "missing-groups": {"error": "owned scan failed"},
        "root-array": [],
        "groups-null": {"groups": None},
        "groups-object": {"groups": {}},
        "row-null": {"groups": [None]},
        "missing-files": {"groups": [{}]},
        "files-string": {"groups": [{"files": str(left)}]},
        "nonpath": {"groups": [{"files": [None, str(left)]}]},
        "empty-path": {"groups": [{"files": ["", str(left)]}]},
        "nul-path": {"groups": [{"files": ["\0", str(left)]}]},
    }
    bodies = {
        **{
            name: f"print({json.dumps(report)!r})\n"
            for name, report in invalid_reports.items()
        },
        "exit": (
            "import sys\nprint('owned engine read failure', file=sys.stderr)\n"
            "sys.exit(3)\n"
        ),
        "vanished": (
            f"from pathlib import Path\nPath({str(right)!r}).unlink()\n"
            f"print({payload!r})\n"
        ),
        "malformed": "print('invalid engine JSON')\n",
    }
    executable = binaries / "fclones"
    executable.write_text(
        f"#!/bin/sh\n'''exec' {shlex.quote(sys.executable)} \"$0\" \"$@\"\n' '''\n"
        + bodies[failure]
    )
    executable.chmod(0o700)
    command = [sys.executable, "-m", "scitex_storage", "find-duplicates", str(source)]
    command += ["--json"] * as_json
    env = dict(os.environ, PATH=str(binaries))
    # Act: invoke the actual CLI, preserving separate stdout and stderr.
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=20
    )
    # Assert: failure stays nonzero, no clean/partial JSON answer is emitted.
    expected = {
        **dict.fromkeys(invalid_reports, "Invalid fclones JSON report"),
        "exit": "`fclones group` exited 3: owned engine read failure",
        "vanished": "A duplicate file changed or could not be inspected",
        "malformed": "Expecting value",
    }
    assert (
        result.returncode,
        result.stdout,
        result.stderr.startswith("Error: "),
        "Traceback" not in result.stderr,
        expected[failure] in result.stderr,
        left.read_bytes(),
        right.exists(),
    ) == (1, "", True, True, True, b"owned duplicate fixture", failure != "vanished")
