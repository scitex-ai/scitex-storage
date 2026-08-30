#!/usr/bin/env python3
"""Stream a sweep's tar to a REMOTE, writing nothing to the source filesystem.

WHY THIS MODULE EXISTS. ``sweep`` materialises its tar beside the source
(``_sweep._sweep_one``), which makes the verb INVERTED: it exists to
relieve a full filesystem and it consumes that same filesystem to do the
job, so it does maximum damage exactly where it is most needed. PR #29
added a free-space preflight, which converts an outage into a clean
refusal -- but refusing is not working. On a filesystem with no headroom,
local sweep is correctly unusable. This module is the other half: the tar
never touches the source filesystem at all, so headroom stops being a
precondition.

WHY A SEPARATE MODULE rather than a branch inside ``_sweep_one``. These
are opposite data flows that happen to share a verb name -- one builds an
artifact locally and deletes beside it, the other pushes bytes off the
host and verifies at the far end. Every safety mechanism differs
(``statvfs`` here is a REMOTE ``df``; verification here is a checksum over
a wire rather than a local stat). ``_archive.py`` was split along exactly
this seam for exactly this reason, and ``_sweep.py`` is at 492 of its 512
lines, so folding a second data flow in would buy a merge conflict and a
module doing two jobs.

WHY A CHECKSUM AND NOT A SIZE. A streamed tar has no local copy to compare
against, and its size is NOT ``sum(member sizes)`` -- tar pads every member
to a 512-byte boundary and adds headers, so a size check would need a
prediction that is fiddly to get right and silently wrong when it is not.
Worse, size agreeing proves only that the right NUMBER of bytes arrived,
not that they are the right bytes. So the stream is hashed IN FLIGHT as it
passes through this process, the remote is asked to hash what it received,
and the two are compared. That is end-to-end verification of the actual
content, which is the same standard ``rsync --append-verify`` meets and
the reason its ``rc=0`` is worth trusting.

THE ORDERING RULE, from the movability card and not negotiable: A COPY IS
NOT A MIGRATION UNTIL THE DESTINATION IS READ BACK. Verify at the
destination, THEN remove at the source, never the reverse. And removal is
opt-in (``remove_source=False``): a verb whose default deletes the only
copy is one flag away from a bad afternoon.
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .._measure._space import remote_free_bytes
from ._sweep import InsufficientSpaceError, SweepCandidate, check_space

#: Read size for the tar->ssh pump. Large enough that the hash and the
#: socket write are not dominated by syscall overhead on a multi-GB tree.
CHUNK_BYTES = 1024 * 1024

#: Remote hash command. `sha256sum` is coreutils AND busybox (the NAS
#: units are busybox, which is why `df -Pk` is spelled portably in
#: _space.py too). Output is "<hex>  <path>", so the hash is field 0.
REMOTE_SHA_CMD = "sha256sum {path}"

#: Remote existence probe. Kept separate from the hash so "already there"
#: is distinguishable from "hash failed" -- collapsing them would let a
#: broken probe read as a free destination.
REMOTE_STAT_CMD = "test -e {path}"


class StreamVerificationError(RuntimeError):
    """The remote artifact's checksum did not match what was sent.

    Its own class because a caller must be able to distinguish it from a
    transport failure. A transport failure means "try again"; this means
    "the bytes at the far end are not the bytes that left here", and the
    source must not be removed on either -- but only this one indicates
    something is actively wrong rather than merely incomplete.
    """


@dataclass(frozen=True)
class StreamedSweep:
    """Result of a verified stream. Every field is a measurement."""

    candidate: SweepCandidate
    destination: str
    remote_path: str
    member_count: int
    bytes_streamed: int
    sha256: str
    source_removed: bool

    def __post_init__(self) -> None:
        # Refuse to exist in a shape that would let a caller read success
        # from an unverified stream -- the same doctrine as Signal
        # refusing to exist without evidence.
        if not self.sha256:
            raise ValueError(
                "StreamedSweep requires a sha256: an unverified stream must "
                "not be representable as a completed one"
            )
        if self.member_count <= 0:
            raise ValueError(
                f"StreamedSweep requires a positive member_count, got "
                f"{self.member_count}: an empty tar is a failure, not a sweep"
            )


def _ssh_argv(destination: str, remote_command: str) -> list[str]:
    """Build an ssh argv reusing the fleet's ControlMaster.

    ControlPath is passed explicitly because the operator's standing
    constraint is ONE connection to a given host (a prior incident put
    enough concurrent sessions on Spartan to risk the administrators'
    attention). Reusing the mux means a stream costs zero new connections.
    """
    return [
        "ssh",
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPath=/tmp/cm-%h:%p",
        "-o",
        "ControlPersist=60",
        destination,
        remote_command,
    ]


def _remote_exists(destination: str, remote_path: str, runner=None) -> bool | None:
    """Three-state: True present, False absent, None could-not-ask.

    None is NOT folded into False. "The destination is free" and "we could
    not find out whether the destination is free" license different
    actions, and only one of them licenses writing.
    """
    run = runner or subprocess.run
    cmd = REMOTE_STAT_CMD.format(path=shlex.quote(remote_path))
    try:
        result = run(
            _ssh_argv(destination, cmd),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    # Anything else is ssh itself failing (255 for connection errors), not
    # `test` answering. Reporting that as "absent" would invite a write to
    # a host we never reached.
    return None


def remote_sha256(destination: str, remote_path: str, runner=None) -> str | None:
    """Hash the artifact AT THE DESTINATION. None when it could not be read.

    None rather than "" so an unreadable file cannot compare equal to
    anything -- an empty string would silently match another empty string.
    """
    run = runner or subprocess.run
    cmd = REMOTE_SHA_CMD.format(path=shlex.quote(remote_path))
    try:
        result = run(
            _ssh_argv(destination, cmd),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fields = result.stdout.split()
    if not fields or len(fields[0]) != 64:
        return None
    return fields[0]


def stream_sweep_to_remote(
    candidate: SweepCandidate,
    destination: str,
    remote_dir: str,
    remove_source: bool = False,
    runner=None,
    spawn=None,
) -> StreamedSweep:
    """Tar ``candidate`` straight to ``destination``, verify, optionally remove.

    NOTHING IS WRITTEN TO THE SOURCE FILESYSTEM. ``tar -cf -`` streams to
    this process, which hashes each chunk and forwards it to ``ssh``, which
    writes it at the far end. That is what makes the verb usable on a
    filesystem with no free space -- the condition under which local sweep
    must correctly refuse.

    Ordering is: preflight the REMOTE -> stream to a temp name -> read the
    checksum back -> rename remotely -> only then remove the source.
    """
    remote_tmp = f"{remote_dir.rstrip('/')}/.{candidate.name}.tar.streaming"
    remote_final = f"{remote_dir.rstrip('/')}/{candidate.name}.tar"

    # Counted UP FRONT, not after the stream: with remove_source=True the
    # tree is gone by then, and a count taken from a deleted directory is
    # the kind of number that quietly becomes 0 and reads as success.
    member_count = _count_members(candidate.path)
    if member_count == 0:
        raise RuntimeError(
            f"refusing to stream {candidate.path}: it holds 0 files "
            "(changed since planning?) -- nothing was transferred and the "
            "source is untouched"
        )

    # 1. REFUSE TO OVERWRITE. Checked before the space probe so a caller
    #    re-running after a success gets "already there", not "no room".
    present = _remote_exists(destination, remote_final, runner=runner)
    if present is None:
        raise RuntimeError(
            f"refusing to stream {candidate.path}: could not determine whether "
            f"{destination}:{remote_final} already exists. This is a "
            f"could-not-look, not a clear destination -- check the ssh path to "
            f"{destination} before retrying."
        )
    if present:
        raise FileExistsError(
            f"refusing to stream {candidate.path}: "
            f"{destination}:{remote_final} already exists"
        )

    # 2. TWO-SIDED PREFLIGHT, against the REMOTE. The comparator is
    #    _sweep.check_space rather than a second implementation: two space
    #    comparators would guarantee drift, and this one already encodes
    #    the headroom margin and the three-state verdict.
    available = remote_free_bytes(destination, remote_dir, runner=runner)
    verdict = check_space(candidate.size, available)
    if verdict.ok is not True:
        raise InsufficientSpaceError(
            f"refusing to stream {candidate.path} to {destination}:{remote_dir}: "
            f"{verdict.detail}. Nothing was transferred. Free space on the "
            f"destination, or choose another one -- unlike local sweep, the "
            f"SOURCE filesystem's free space is irrelevant here."
        )

    # 3. STREAM. tar's stdout is pumped through this process so the bytes
    #    can be hashed exactly as they are sent. Piping tar directly into
    #    ssh would be marginally faster and would leave nothing to compare
    #    the remote hash against.
    run = runner or subprocess.run
    tar_argv = [
        "tar",
        "-C",
        str(candidate.path.parent),
        "-cf",
        "-",
        candidate.name,
    ]
    ssh_argv = _ssh_argv(destination, f"cat > {shlex.quote(remote_tmp)}")

    # `spawn` is injectable for the SAME reason `runner` is: without it a
    # test asserting "the refusal transferred nothing" cannot observe the
    # transfer at all, so it passes whatever the code does. A check that
    # cannot see the thing it guards is not a check.
    launch = spawn or subprocess.Popen
    hasher = hashlib.sha256()
    bytes_streamed = 0
    tar_proc = launch(tar_argv, stdout=subprocess.PIPE)
    ssh_proc = launch(ssh_argv, stdin=subprocess.PIPE)
    try:
        assert tar_proc.stdout is not None and ssh_proc.stdin is not None
        while True:
            chunk = tar_proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
            bytes_streamed += len(chunk)
            ssh_proc.stdin.write(chunk)
        ssh_proc.stdin.close()
    finally:
        if tar_proc.stdout is not None:
            tar_proc.stdout.close()
        tar_rc = tar_proc.wait()
        ssh_rc = ssh_proc.wait()

    if tar_rc != 0 or ssh_rc != 0:
        _remote_unlink(destination, remote_tmp, runner=runner)
        raise RuntimeError(
            f"refusing to complete {candidate.path}: tar exited {tar_rc}, ssh "
            f"exited {ssh_rc}. The partial remote artifact {remote_tmp} was "
            f"removed and the SOURCE IS UNTOUCHED."
        )

    # 4. READ BACK. The whole point: a copy is not a migration until the
    #    destination has been read.
    local_digest = hasher.hexdigest()
    remote_digest = remote_sha256(destination, remote_tmp, runner=runner)
    if remote_digest is None:
        _remote_unlink(destination, remote_tmp, runner=runner)
        raise StreamVerificationError(
            f"could not read back {destination}:{remote_tmp} to verify it. "
            f"This is a COULD-NOT-LOOK, not a mismatch -- the transfer may "
            f"well be fine -- but the source is NOT removed on an unverified "
            f"stream, and the partial artifact was cleaned up."
        )
    if remote_digest != local_digest:
        _remote_unlink(destination, remote_tmp, runner=runner)
        raise StreamVerificationError(
            f"checksum MISMATCH for {candidate.path}: sent {local_digest}, "
            f"destination has {remote_digest}. The remote artifact was removed "
            f"and the source is untouched. This is not a retryable transport "
            f"error -- something altered the bytes."
        )

    # 5. Only now is the artifact real. Rename remotely so a partial
    #    stream can never be mistaken for a finished one by a later run.
    rename = run(
        _ssh_argv(
            destination,
            f"mv {shlex.quote(remote_tmp)} {shlex.quote(remote_final)}",
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if rename.returncode != 0:
        raise RuntimeError(
            f"streamed and VERIFIED {candidate.path} but could not rename "
            f"{remote_tmp} -> {remote_final} ({rename.stderr.strip()}). The "
            f"data is safe at the temp name; the source is untouched."
        )

    # 6. Source removal LAST, opt-in, and only on a verified stream.
    source_removed = False
    if remove_source:
        import shutil

        shutil.rmtree(candidate.path)
        source_removed = True

    return StreamedSweep(
        candidate=candidate,
        destination=destination,
        remote_path=remote_final,
        member_count=member_count,
        bytes_streamed=bytes_streamed,
        sha256=local_digest,
        source_removed=source_removed,
    )


def _remote_unlink(destination: str, remote_path: str, runner=None) -> None:
    """Best-effort cleanup of a partial artifact. Never raises.

    A failure here must not mask the error that caused the cleanup -- the
    caller is already raising something more informative, and losing that
    to a secondary ssh failure would replace a diagnosis with a mystery.
    """
    run = runner or subprocess.run
    try:
        run(
            _ssh_argv(destination, f"rm -f {shlex.quote(remote_path)}"),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _count_members(path: Path) -> int:
    """Count regular files, matching _sweep's traversal doctrine.

    Never descends symlinked directories -- same rule as ``scan()`` and
    ``_sweep_one``, so the member count means the same thing across the
    package rather than being subtly different per call site.
    """
    import os

    count = 0
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        dirnames[:] = [
            d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
        ]
        count += len(filenames)
    return count

# EOF
