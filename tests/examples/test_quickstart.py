"""PS303 example mirror stub: ensure examples/quickstart.py runs cleanly."""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "quickstart.py"


def test_quickstart_example_file_compiles_without_syntax_errors():
    # Arrange
    example = EXAMPLE
    # Act
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", str(example)],
        capture_output=True,
    )
    # Assert
    assert proc.returncode == 0, (
        f"py_compile failed for {example}: "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def _run_example() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXAMPLE)],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.requires_fd
def test_quickstart_example_exits_zero():
    # Arrange
    example = EXAMPLE
    # Act
    proc = _run_example()
    # Assert
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


@pytest.mark.requires_fd
def test_quickstart_example_reports_the_large_child_as_biggest():
    # Arrange
    example = EXAMPLE
    # Act
    proc = _run_example()
    # Assert
    assert "biggest child: big-data" in proc.stdout
