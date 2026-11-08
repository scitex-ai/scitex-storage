"""Deprecation warnings use the actual calling process, not exported state."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "exported_parent", [{}, {"PPID": "123456789"}, {"PPID": "stale-shell-value"}],
)
def test_warning_uses_actual_parent_and_remains_once_per_parent(
    tmp_path, exported_parent,
):
    # Arrange -- invoke the real helper twice in one real child process.
    environment = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path))
    environment.pop("PPID", None)
    environment.update(exported_parent)
    script = """
import json, os
from pathlib import Path
from scitex_storage._cli._gui_cmd import _warn_once_forward
_warn_once_forward('start-gui', 'gui open', '1.0')
_warn_once_forward('start-gui', 'gui open', '1.0')
print(json.dumps({'parent': os.getppid(), 'markers': sorted(
    p.name for p in Path(os.environ['XDG_RUNTIME_DIR']).iterdir())}))
"""
    # Act
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment,
        capture_output=True, text=True, timeout=20, check=True,
    )
    observed = json.loads(result.stdout)
    # Assert -- the caller's kernel PID keys exactly one warning marker.
    assert (
        len(observed["markers"]),
        observed["markers"][0].endswith(f"-{observed['parent']}-start-gui.flag"),
        result.stderr.count("'start-gui' is deprecated"),
    ) == (1, True, 1)
