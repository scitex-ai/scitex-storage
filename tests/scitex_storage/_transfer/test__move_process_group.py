"""Real orphaned process-group liveness, separate from transfer data fixtures."""

import subprocess
import sys

from tests.scitex_storage._transfer.test__move import _process_is_running


def test_group_liveness_includes_child_after_group_leader_exit(tmp_path):
    # Arrange
    import os
    import signal

    from scitex_storage._transfer._move import (
        _process_group_is_running,
        _terminate_group,
    )

    pid_file = tmp_path / "orphan.pid"
    script = tmp_path / "owned_leader.py"
    script.write_text(
        "import subprocess,sys\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-I','-S','-c',"
        "'import time;time.sleep(60)'])\n"
        "Path(sys.argv[1]).write_text(str(child.pid))\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", str(script), str(pid_file)],
        start_new_session=True,
    )
    # Act
    try:
        process.wait(timeout=5)
        child = int(pid_file.read_text())
        live_without_leader = _process_group_is_running(process.pid)
        _terminate_group(process)
        stopped = not _process_is_running(child)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    # Assert
    assert (
        live_without_leader and stopped and not _process_group_is_running(process.pid)
    )


def test_unacknowledged_group_inspection_budget_stays_pending():
    # Arrange
    import os
    import time

    import pytest

    from scitex_storage._transfer._move import (
        _process_group_is_running,
        _TerminationPending,
    )

    expired = time.monotonic() - 1
    # Act
    # Assert
    with pytest.raises(_TerminationPending, match="deadline"):
        _process_group_is_running(os.getpgrp(), deadline=expired)
