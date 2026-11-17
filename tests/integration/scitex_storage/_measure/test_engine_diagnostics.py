"""Actual engine protocol diagnostics never masquerade as a complete scan."""

import json
import os
import shlex
import subprocess
import sys

import pytest

from scitex_storage._measure._duplicate_protocol import (
    _check_engine_diagnostics,
    _reported_groups,
)


@pytest.mark.parametrize(
    "stderr",
    [
        b"",
        b"\n \n",
        b"[2026-11-18] fclones:  info: Found 0 candidates\n",
        b"\x1b[32m[2026-11-18] fclones:  info:\x1b[0m Found 2 files\n",
    ],
)
def test_known_info_diagnostics_are_accepted(stderr):
    # Arrange
    diagnostics = stderr
    # Act
    result = _check_engine_diagnostics(diagnostics)
    # Assert
    assert result is None


@pytest.mark.parametrize(
    "stderr",
    [
        b"[2026-11-18] fclones: warn: Failed to read dir: Permission denied\n",
        b"[2026-11-18] fclones: error: Failed reading file\n",
        b"unknown diagnostic\n",
        b"[2026-11-18] fclones: debug: unexpected level\n",
        (
            b"[2026-11-18] fclones: info: Scan started\n"
            b"[2026-11-18] fclones: warn: incomplete\n"
        ),
    ],
)
def test_non_info_diagnostics_refuse_complete_verdict(stderr):
    # Arrange
    diagnostics = stderr
    # Act
    # Assert
    with pytest.raises(RuntimeError, match="Incomplete fclones scan"):
        _check_engine_diagnostics(diagnostics)


@pytest.mark.parametrize("missing", [1, 10])
def test_reported_missing_files_refuse_complete_verdict(missing):
    # Arrange
    payload = {"groups": [], "header": {"stats": {"missing_file_count": missing}}}
    # Act
    # Assert
    with pytest.raises(RuntimeError, match="Incomplete fclones scan"):
        _reported_groups(payload)


@pytest.mark.parametrize("missing", [True, -1, "0", None, 1.5])
def test_malformed_missing_count_is_refused(missing):
    # Arrange
    payload = {"groups": [], "header": {"stats": {"missing_file_count": missing}}}
    # Act
    # Assert
    with pytest.raises(RuntimeError, match="Invalid fclones JSON report"):
        _reported_groups(payload)


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("diagnostic", ["warn", "unstructured"])
def test_zero_exit_engine_diagnostic_refuses_cli_success(tmp_path, as_json, diagnostic):
    # Arrange -- a real executable controls only the external protocol.
    source = tmp_path / "source"
    source.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    text = {
        "warn": "[2026-11-18] fclones: warn: owned inspection failure",
        "unstructured": "owned unknown engine diagnostic",
    }[diagnostic]
    body = (
        f"import sys\nprint({json.dumps({'groups': []})!r})\n"
        f"print({text!r}, file=sys.stderr)\n"
    )
    executable = binaries / "fclones"
    executable.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -c "
        + shlex.quote(body)
        + "\n"
    )
    executable.chmod(0o755)
    command = [sys.executable, "-m", "scitex_storage", "find-duplicates", str(source)]
    command += ["--json"] * as_json
    # Act
    result = subprocess.run(
        command,
        env=dict(os.environ, PATH=str(binaries)),
        capture_output=True,
        text=True,
        timeout=20,
    )
    # Assert
    assert (
        result.returncode,
        result.stdout,
        "Incomplete fclones scan" in result.stderr,
        "Traceback" in result.stderr,
    ) == (1, "", True, False)
