"""What does `sweep` leave behind when the write dies PARTWAY?

Card sweep-writes-tar-to-source-filesystem-20260722 asked for a
constrained-filesystem test, and the existing tests do not answer this.
They declare a candidate larger than the whole filesystem and check that
sweep REFUSES before starting. Refusing up front and behaving correctly
when space runs out DURING the write are different properties, and only
the second one says what state the partial artifact and the source are
left in.

HOW THE FAILURE IS INDUCED, and the honest caveat, stated here rather
than buried: a bounded tmpfs would be the exact fixture, but mounting one
needs superuser and this container is not (measured: "must be superuser
to use mount"). `RLIMIT_FSIZE` is per-process, settable from userspace,
and KERNEL-ENFORCED, so the write fails for real rather than by
simulation -- measured: a child capped at 4 KiB writing 64 KiB gets a
genuine OSError and leaves a 4096-byte partial file.

The errno is EFBIG, not ENOSPC. Different cause, same shape: a write that
succeeds for N bytes and then fails. What is under test is sweep's
BEHAVIOUR when a write dies mid-stream -- is the partial
`.<name>.tar.sweeping` cleaned up, is the source still intact -- and that
does not depend on which errno stopped it. Claiming this reproduces a
full disk would be the same overclaim as reading a positive control as
proof of coverage.

The cap is applied in a FORKED CHILD, never in-process: a stray rlimit
would otherwise break every later test in the session in ways that look
like unrelated failures.
"""

from __future__ import annotations

import os
import resource
from pathlib import Path

import pytest

from scitex_storage._transfer._sweep import SweepCandidate, _sweep_one

#: Small enough that a tar of the fixture cannot fit, large enough that
#: tarfile gets a header written before it dies -- so the failure lands
#: mid-write rather than on the very first byte.
FSIZE_CAP = 4096

CHILD_OK = 0
CHILD_OSERROR = 3
CHILD_OTHER = 4


def _candidate(tmp_path: Path) -> SweepCandidate:
    """A directory whose tar will comfortably exceed FSIZE_CAP."""
    target = tmp_path / "runs"
    target.mkdir()
    for i in range(8):
        (target / f"f{i}.bin").write_bytes(b"\x5a" * 8192)
    return SweepCandidate(
        name="runs",
        path=target,
        file_count=8,
        size=8 * 8192,
        newest_mtime=0.0,
    )


def _sweep_under_fsize_cap(candidate: SweepCandidate) -> int:
    """Run `_sweep_one` in a child capped by RLIMIT_FSIZE; return its status.

    Returns the child's exit code, so the caller can assert on the failure
    mode, and leaves the filesystem in whatever state sweep left it -- which
    is the thing actually being examined.
    """
    _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    pid = os.fork()
    if pid == 0:  # pragma: no cover -- child never returns to pytest
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_CAP, hard))
            _sweep_one(candidate)
            os._exit(CHILD_OK)
        except OSError:
            os._exit(CHILD_OSERROR)
        except BaseException:
            os._exit(CHILD_OTHER)
    _, status = os.waitpid(pid, 0)
    if os.WIFSIGNALED(status):
        # SIGXFSZ is a legitimate way for the cap to bite; treat it as the
        # same outcome rather than as a mystery.
        return CHILD_OSERROR
    return os.WEXITSTATUS(status)


def test_a_write_that_dies_partway_does_not_report_success(tmp_path):
    # Arrange
    candidate = _candidate(tmp_path)

    # Act
    status = _sweep_under_fsize_cap(candidate)

    # Assert
    assert status != CHILD_OK


def test_a_write_that_dies_partway_LEAVES_THE_SOURCE_INTACT(tmp_path):
    # The reversibility invariant sweep's own docstring claims: the source
    # is removed only after the artifact is complete and renamed.
    # Arrange
    candidate = _candidate(tmp_path)

    # Act
    _sweep_under_fsize_cap(candidate)

    # Assert
    assert candidate.path.is_dir()


def test_a_write_that_dies_partway_KEEPS_EVERY_SOURCE_FILE(tmp_path):
    # "The directory still exists" is weaker than "nothing was lost".
    # Arrange
    candidate = _candidate(tmp_path)

    # Act
    _sweep_under_fsize_cap(candidate)

    # Assert
    assert len(list(candidate.path.iterdir())) == 8


def test_a_write_that_dies_partway_REMOVES_THE_PARTIAL_ARTIFACT(tmp_path):
    # This is the one the card cares about: a partial
    # `.<name>.tar.sweeping` left on a filesystem that just ran out of room
    # is "space consumed, nothing accomplished" -- the worst outcome, since
    # it takes the last free bytes AND achieves nothing.
    # Arrange
    candidate = _candidate(tmp_path)
    partial = candidate.path.parent / f".{candidate.name}.tar.sweeping"

    # Act
    _sweep_under_fsize_cap(candidate)

    # Assert
    assert not partial.exists()


def test_a_write_that_dies_partway_LEAVES_NO_FINISHED_TAR(tmp_path):
    # A .tar without the .sweeping suffix would mean the rename happened,
    # which would mean sweep believed it had succeeded.
    # Arrange
    candidate = _candidate(tmp_path)
    finished = candidate.path.parent / f"{candidate.name}.tar"

    # Act
    _sweep_under_fsize_cap(candidate)

    # Assert
    assert not finished.exists()


@pytest.mark.skipif(
    not hasattr(resource, "RLIMIT_FSIZE"), reason="no RLIMIT_FSIZE on this platform"
)
def test_the_cap_itself_actually_bites(tmp_path):
    # POSITIVE CONTROL for the fixture. If the cap silently did nothing,
    # every test above would pass for the wrong reason -- sweep would have
    # succeeded and the assertions about "no leftover artifact" would hold
    # trivially. Verify the mechanism independently of sweep.
    # Arrange
    probe = tmp_path / "probe.bin"
    _soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)

    # Act
    pid = os.fork()
    if pid == 0:  # pragma: no cover -- child never returns to pytest
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_CAP, hard))
            with open(probe, "wb") as handle:
                handle.write(b"\0" * (FSIZE_CAP * 16))
                handle.flush()
            os._exit(CHILD_OK)
        except BaseException:
            os._exit(CHILD_OSERROR)
    _, status = os.waitpid(pid, 0)
    outcome = CHILD_OSERROR if os.WIFSIGNALED(status) else os.WEXITSTATUS(status)

    # Assert
    assert outcome == CHILD_OSERROR

# EOF
