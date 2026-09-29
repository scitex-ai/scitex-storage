"""Isolated environment for e2e tests (mirrors tests/smoke/conftest.py)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


def _console_script() -> str:
    """Resolve the ``scitex-storage`` entry point for this interpreter."""
    sibling = Path(sys.executable).with_name("scitex-storage")
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("scitex-storage")
    if found is not None:
        return found
    raise AssertionError("scitex-storage console script not found")


CLI = _console_script()


@pytest.fixture
def isolated_cli_env(tmp_path):
    """Env dict with ``HOME`` redirected at a tmp dir (no global mutation)."""
    # Arrange — copy, never mutate: the parent process environment stays
    # exactly as pytest received it.
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    return env
