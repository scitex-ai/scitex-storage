"""Real filesystem errors must not turn a size-only walk into a clean report."""

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main
from scitex_storage._measure._duplicates import _collect_file_sizes


def test_size_walk_reports_a_non_directory(tmp_path):
    # Arrange
    file = tmp_path / "file"
    file.write_bytes(b"data")
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        _collect_file_sizes(str(file), 0, None, {}, set())


def test_size_walk_reports_a_parent_symlink_loop(tmp_path):
    # Arrange
    loop = tmp_path / "loop"
    loop.symlink_to(loop, target_is_directory=True)
    # Act
    # Assert
    with pytest.raises(OSError):
        _collect_file_sizes(str(loop / "child"), 0, None, {}, set())


def test_size_walk_still_ignores_a_vanished_directory(tmp_path):
    # Arrange
    groups = {}
    seen = set()
    # Act
    result = _collect_file_sizes(str(tmp_path / "gone"), 0, None, groups, seen)
    # Assert
    assert (result, groups, seen) == (None, {}, set())


@pytest.mark.parametrize(
    "options,prefix", [([], ""), (["--sizes-only"], "Size-only scan failed: ")]
)
@pytest.mark.parametrize("output", [[], ["--json"]])
def test_duplicate_cli_reports_invalid_directory_without_a_traceback(
    tmp_path, options, prefix, output
):
    # Arrange
    file = tmp_path / "file"
    file.write_bytes(b"data")
    # Act
    result = CliRunner().invoke(main, ["find-duplicates", str(file), *options, *output])
    # Assert
    assert {
        "exit": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "raw_os_error": isinstance(result.exception, OSError),
        "input": file.read_bytes(),
    } == {
        "exit": 1,
        "stdout": "",
        "stderr": f"Error: {prefix}not a directory: {file}\n",
        "raw_os_error": False,
        "input": b"data",
    }
