"""E2E layer: one realistic end-to-end story against real subsystems.

``scan`` shells out to the real ``fd`` binary against a real (tmp)
directory tree — no network, loopback only. Skipped per-test when ``fd``
is absent (subsystem-aware skip, never an env gate). One behavior per
test, exactly one ``assert`` each (PA-307).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from .conftest import CLI

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("fd") is None and shutil.which("fdfind") is None,
        reason="requires the `fd` binary on PATH (apt: fd-find)",
    ),
]


def test_scan_json_reports_a_real_tmp_tree(tmp_path, isolated_cli_env):
    # Arrange — a real tree with real files for the real binary to walk.
    target = tmp_path / "project"
    (target / "data").mkdir(parents=True)
    (target / "data" / "sample.bin").write_bytes(b"\x00" * 4096)
    (target / "notes.txt").write_text("field notes\n")
    cmd = [CLI, "scan", str(target), "--json"]
    # Act — run the full CLI end to end, capturing both streams.
    proc = subprocess.run(cmd, capture_output=True, text=True, env=isolated_cli_env, timeout=120)
    # Assert — a single verdict: clean exit and the tree shows up.
    assert proc.returncode == 0 and "notes.txt" in proc.stdout
