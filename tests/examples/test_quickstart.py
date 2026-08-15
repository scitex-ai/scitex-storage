"""PS303 example mirror stub: ensure examples/quickstart.py runs cleanly."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "quickstart.py"

# quickstart.py calls scan(), which shells out to `fd`. Only the RUN needs it —
# the py_compile test below stays unguarded, because syntax is checkable
# without any binary and losing that check to an environment gap would be a
# real loss of coverage.
#
# SKIP, never silently pass: without `fd` the example exits non-zero BY DESIGN
# (MissingSystemDependencyError is this package's promised contract for a
# missing binary). Asserting exit 0 there tests the environment, not the code.
_FD_BIN = shutil.which("fd") or shutil.which("fdfind")
_needs_fd = pytest.mark.skipif(
    _FD_BIN is None,
    reason="needs the `fd` binary (fd-find); quickstart calls scan()",
)


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


@_needs_fd
def test_quickstart_example_exits_zero():
    # Arrange
    example = EXAMPLE
    # Act
    proc = _run_example()
    # Assert
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


@_needs_fd
def test_quickstart_example_reports_the_large_child_as_biggest():
    # Arrange
    example = EXAMPLE
    # Act
    proc = _run_example()
    # Assert
    assert "biggest child: big-data" in proc.stdout
