"""Startup guidance and diagnostics must retain their separate stream contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import scitex_storage


def _probe(tmp_path: Path, body: str):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "SCITEX_DIR": str(tmp_path / "scitex-state"),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(
            path for path in (
                str(Path(scitex_storage.__file__).resolve().parent.parent),
                os.environ.get("PYTHONPATH", ""),
            ) if path
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONFAULTHANDLER": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if "LD_LIBRARY_PATH" in os.environ:
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
    script = (
        "import sys\n"
        "def forbid_socket(event, args):\n"
        "    if event.startswith('socket.'):\n"
        "        raise AssertionError('logging probe must not open sockets')\n"
        "sys.addaudithook(forbid_socket)\n"
        "import scitex_logging as logging\n"
        "logging.configure(level='info', enable_file=False, "
        "capture_prints=False)\n"
        "from scitex_storage._django import _server as server\n"
        "diagnostics = logging.getLogger(server.__name__)\n" + body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_banner_respects_level_changed_after_import(tmp_path):
    # Arrange
    code = "logging.set_level('error')\nserver._print_banner('127.0.0.1', 5051)\n"
    # Act
    result = _probe(tmp_path, code)
    # Assert
    assert all((result.returncode == 0, result.stdout == "", result.stderr == "")), {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def test_banner_recovers_when_level_is_lowered(tmp_path):
    # Arrange
    code = (
        "logging.set_level('error')\n"
        "server._print_banner('127.0.0.1', 5051)\n"
        "logging.set_level('info')\n"
        "server._print_banner('127.0.0.1', 5051)\n"
    )
    # Act
    result = _probe(tmp_path, code)
    # Assert
    assert all(
        (
            result.returncode == 0,
            result.stderr == "",
            result.stdout.count("SciTeX Storage GUI:") == 1,
            "http://127.0.0.1:5051" in result.stdout,
            "scitex-storage gui stop" in result.stdout,
            "scitex-storage gui status" in result.stdout,
        )
    ), {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def test_banner_does_not_reroute_or_duplicate_diagnostics(tmp_path):
    # Arrange
    code = (
        "server._print_banner('127.0.0.1', 5051)\n"
        "server._print_banner('127.0.0.1', 5051)\n"
        "diagnostics.warning('GUI dependency unavailable')\n"
    )
    # Act
    result = _probe(tmp_path, code)
    # Assert
    assert all(
        (
            result.returncode == 0,
            result.stdout.count("SciTeX Storage GUI:") == 2,
            "GUI dependency unavailable" not in result.stdout,
            result.stderr.count("GUI dependency unavailable") == 1,
        )
    ), {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def test_diagnostics_follow_configured_threshold(tmp_path):
    # Arrange
    code = (
        "server._print_banner('127.0.0.1', 5051)\n"
        "logging.set_level('error')\n"
        "diagnostics.warning('suppressed warning')\n"
        "diagnostics.error('visible error')\n"
    )
    # Act
    result = _probe(tmp_path, code)
    # Assert
    assert all(
        (
            result.returncode == 0,
            "suppressed warning" not in result.stdout + result.stderr,
            "visible error" not in result.stdout,
            result.stderr.count("visible error") == 1,
        )
    ), {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
