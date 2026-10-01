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
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": str(Path(scitex_storage.__file__).resolve().parent.parent),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
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
        + body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_banner_respects_level_changed_after_import(tmp_path):
    result = _probe(
        tmp_path,
        "logging.set_level('error')\nserver._print_banner('127.0.0.1', 5051)\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_banner_recovers_when_level_is_lowered(tmp_path):
    result = _probe(
        tmp_path,
        "logging.set_level('error')\nserver._print_banner('127.0.0.1', 5051)\n"
        "logging.set_level('info')\nserver._print_banner('127.0.0.1', 5051)\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.count("SciTeX Storage GUI:") == 1
    assert "http://127.0.0.1:5051" in result.stdout
    assert "scitex-storage gui stop" in result.stdout
    assert "scitex-storage gui status" in result.stdout


def test_banner_does_not_reroute_or_duplicate_diagnostics(tmp_path):
    result = _probe(
        tmp_path,
        "server._print_banner('127.0.0.1', 5051)\n"
        "server._print_banner('127.0.0.1', 5051)\n"
        "cause = ImportError('missing shell')\n"
        "server.log.warning(server.bare_django_warning(cause))\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("SciTeX Storage GUI:") == 2
    assert "BARE DJANGO" not in result.stdout
    assert result.stderr.count("BARE DJANGO") == 1
    assert "missing shell" in result.stderr
    assert "pip install scitex-app" in result.stderr


def test_diagnostics_follow_configured_threshold(tmp_path):
    result = _probe(
        tmp_path,
        "server._print_banner('127.0.0.1', 5051)\n"
        "logging.set_level('error')\nserver.log.warning('suppressed warning')\n"
        "server.log.error('visible error')\n",
    )
    assert result.returncode == 0, result.stderr
    assert "suppressed warning" not in result.stdout + result.stderr
    assert "visible error" not in result.stdout
    assert result.stderr.count("visible error") == 1
