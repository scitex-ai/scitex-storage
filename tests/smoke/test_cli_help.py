"""Smoke layer: the binary launches and the obvious paths don't crash.

Fast (<60s total), subprocess-driven CLI happy paths, run on every PR.
One behavior per test, exactly one ``assert`` each (PA-307).
"""

from __future__ import annotations

import subprocess

import pytest

from .conftest import CLI

pytestmark = pytest.mark.smoke


def test_top_level_help_exits_clean(isolated_cli_env):
    # Arrange — the real console script under an isolated HOME.
    cmd = [CLI, "--help"]
    # Act — run it as a user would, capturing both streams.
    proc = subprocess.run(cmd, capture_output=True, text=True, env=isolated_cli_env, timeout=60)
    # Assert — a single verdict: it launched and exited clean.
    assert proc.returncode == 0 and "scan" in proc.stdout


def test_scan_help_exits_clean(isolated_cli_env):
    # Arrange — the read-only verb's help under an isolated HOME.
    cmd = [CLI, "scan", "--help"]
    # Act — run it as a user would, capturing both streams.
    proc = subprocess.run(cmd, capture_output=True, text=True, env=isolated_cli_env, timeout=60)
    # Assert — a single verdict: it launched and exited clean.
    assert proc.returncode == 0 and "--json" in proc.stdout
