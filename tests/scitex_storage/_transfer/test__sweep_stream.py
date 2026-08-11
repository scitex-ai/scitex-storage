"""Streaming a sweep to a remote: nothing local, verified at the far end.

This closes FIX 2 on card sweep-writes-tar-to-source-filesystem-20260722,
open since 2026-07-22 and the only item on it that REMOVES the inversion
rather than mitigating it. Local sweep writes its tar beside the source, so
it consumes the very filesystem it was called to relieve; PR #29's
preflight made it refuse rather than fill the disk, but refusing is not
working.

The tests are built around the failures that would actually hurt:
  - a mismatch or an unreadable read-back must NEVER remove the source
  - a refusal must transfer NOTHING (asserted against an observable seam)
  - "could not ask" must never be read as "the destination is clear"

`_raised` exists so each test keeps ONE assertion while still driving the
error path: `with pytest.raises(...)` plus a follow-up `assert` counts as
two, and splitting a behaviour across two tests to satisfy a counter would
hide that the refusal and its consequence are one fact.

Real tmp_path trees and recording fakes at the process boundary; no
`monkeypatch`, which this repo bans.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path

from scitex_storage._transfer._sweep import InsufficientSpaceError, SweepCandidate
from scitex_storage._transfer._sweep_stream import (
    StreamVerificationError,
    remote_sha256,
    stream_sweep_to_remote,
)


def _raised(fn) -> BaseException | None:
    """Run ``fn`` and return whatever it raised, or None if it did not."""
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - the exception IS the result
        return exc
    return None


class FakeRun:
    """Records every ssh argv and replies per command keyword.

    Reads intent off the REMOTE COMMAND STRING rather than by call index,
    so inserting a step does not silently re-point every canned reply at
    the wrong call. A double whose correctness depends on call order breaks
    the moment the code improves.
    """

    def __init__(self, replies: dict[str, tuple[int, str]]):
        self.replies = replies
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        remote_cmd = argv[-1]
        for key, (rc, out) in self.replies.items():
            if key in remote_cmd:
                return subprocess.CompletedProcess(argv, rc, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def ran(self, keyword: str) -> bool:
        return any(keyword in c[-1] for c in self.calls)


class _Sink:
    def __init__(self, buf: bytearray):
        self.buf = buf

    def write(self, chunk):
        self.buf.extend(chunk)

    def close(self):
        pass


class _DeadProc:
    def __init__(self, stdout=None, stdin=None, rc: int = 0):
        self.stdout = stdout
        self.stdin = stdin
        self._rc = rc

    def wait(self):
        return self._rc


class FakeSpawn:
    """Records every process LAUNCH and captures what was written to ssh.

    Without this seam, "the refusal transferred nothing" would be asserted
    against a `subprocess.Popen` the test cannot see -- so it would pass
    whether or not a transfer happened. A vacuous guard is worse than none:
    it reports coverage it does not have. (My first version of this file
    did exactly that.)

    `real_tar=True` runs the ACTUAL tar so the streamed bytes, and thus the
    in-flight hash, are genuine; only the ssh side is faked.
    """

    def __init__(self, real_tar: bool = False, ssh_rc: int = 0):
        self.launched: list[list[str]] = []
        self.written = bytearray()
        self.real_tar = real_tar
        self.ssh_rc = ssh_rc

    def __call__(self, argv, **kwargs):
        self.launched.append(list(argv))
        if argv[0] == "tar":
            if self.real_tar:
                return subprocess.Popen(argv, **kwargs)
            return _DeadProc(stdout=io.BytesIO(b""))
        return _DeadProc(stdin=_Sink(self.written), rc=self.ssh_rc)

    def launched_tar(self) -> bool:
        return any(a[0] == "tar" for a in self.launched)

    def launched_ssh_write(self) -> bool:
        return any(a[0] == "ssh" and "cat >" in a[-1] for a in self.launched)


class EchoRun(FakeRun):
    """A FakeRun whose remote sha256sum AGREES with what was really sent."""

    def __init__(self, replies, digest_holder: dict[str, str]):
        super().__init__(replies)
        self.digest_holder = digest_holder

    def __call__(self, argv, **kwargs):
        if "sha256sum" in argv[-1]:
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 0, f"{self.digest_holder['digest']}  /share/dest/x\n", ""
            )
        return super().__call__(argv, **kwargs)


def _tree(root: Path, name: str = "cand", files: int = 3) -> SweepCandidate:
    d = root / name
    d.mkdir(parents=True)
    for i in range(files):
        (d / f"f{i}.txt").write_text(f"content-{i}\n")
    size = sum(f.stat().st_size for f in d.iterdir())
    return SweepCandidate(
        name=name, path=d, file_count=files, size=size, newest_mtime=0.0
    )


def _df(free_kb: int) -> str:
    return (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        f"/dev/sda 1000000 0 {free_kb} 1% /share\n"
    )


def _real_tar_digest(cand: SweepCandidate) -> str:
    return hashlib.sha256(
        subprocess.run(
            ["tar", "-C", str(cand.path.parent), "-cf", "-", cand.name],
            capture_output=True,
        ).stdout
    ).hexdigest()


# --- the destination probe is three-state -------------------------------
def test_an_unaskable_destination_is_not_treated_as_clear(tmp_path):
    # ssh returning 255 means we never reached the host. Reading that as
    # "the file is not there" would license writing to a host we could not
    # contact -- the could-not-look folded into the convenient pole.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (255, "")})

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert "could not determine" in str(err)


def test_an_unaskable_destination_transfers_nothing(tmp_path):
    # The load-bearing half: refusing AFTER pushing data would defeat the
    # purpose of a preflight entirely.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (255, "")})
    spawn = FakeSpawn()

    # Act
    _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=spawn
        )
    )

    # Assert
    assert not spawn.launched_tar()


def test_an_existing_remote_artifact_is_refused(tmp_path):
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (0, "")})

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert isinstance(err, FileExistsError)


# --- the preflight measures the REMOTE ----------------------------------
def test_a_full_destination_is_refused_before_any_transfer(tmp_path):
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (1, ""), "df -Pk": (0, _df(free_kb=0))})
    spawn = FakeSpawn()

    # Act
    _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=spawn
        )
    )

    # Assert
    assert not spawn.launched_ssh_write()


def test_a_full_destination_raises_insufficient_space(tmp_path):
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (1, ""), "df -Pk": (0, _df(free_kb=0))})

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert isinstance(err, InsufficientSpaceError)


def test_the_refusal_says_the_source_filesystem_is_irrelevant(tmp_path):
    # The message must teach what distinguishes this verb from local sweep,
    # because the person reading it is standing on a full disk and needs to
    # know THIS path does not care about that.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun({"test -e": (1, ""), "df -Pk": (0, _df(free_kb=0))})

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert "SOURCE filesystem's free space is irrelevant" in str(err)


# --- verification decides whether the source may be removed -------------
def test_a_checksum_mismatch_raises_verification_error(tmp_path):
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun(
        {
            "test -e": (1, ""),
            "df -Pk": (0, _df(free_kb=10_000_000)),
            "sha256sum": (0, f"{'0' * 64}  /share/dest/x\n"),
        }
    )

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert isinstance(err, StreamVerificationError)


def test_a_checksum_mismatch_leaves_the_source_intact(tmp_path):
    # THE test this module exists for. remove_source=True is passed
    # explicitly: even when the caller ASKED for removal, an unverified
    # stream must not remove anything. Verify at the destination, THEN
    # remove at the source -- never the reverse.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun(
        {
            "test -e": (1, ""),
            "df -Pk": (0, _df(free_kb=10_000_000)),
            "sha256sum": (0, f"{'0' * 64}  /share/dest/x\n"),
        }
    )

    # Act
    _raised(
        lambda: stream_sweep_to_remote(
            cand,
            "nas2",
            "/share/dest",
            remove_source=True,
            runner=fake,
            spawn=FakeSpawn(),
        )
    )

    # Assert
    assert sorted(p.name for p in cand.path.iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]


def test_an_unreadable_readback_is_a_could_not_look_not_a_pass(tmp_path):
    # A failed remote sha256sum says NOTHING about the bytes. The transfer
    # may well be fine -- and the source still must not be removed, because
    # "probably fine" is not verification.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun(
        {
            "test -e": (1, ""),
            "df -Pk": (0, _df(free_kb=10_000_000)),
            "sha256sum": (1, ""),
        }
    )

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand,
            "nas2",
            "/share/dest",
            remove_source=True,
            runner=fake,
            spawn=FakeSpawn(),
        )
    )

    # Assert
    assert "COULD-NOT-LOOK" in str(err)


def test_an_unreadable_readback_leaves_the_source_intact(tmp_path):
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun(
        {
            "test -e": (1, ""),
            "df -Pk": (0, _df(free_kb=10_000_000)),
            "sha256sum": (1, ""),
        }
    )

    # Act
    _raised(
        lambda: stream_sweep_to_remote(
            cand,
            "nas2",
            "/share/dest",
            remove_source=True,
            runner=fake,
            spawn=FakeSpawn(),
        )
    )

    # Assert
    assert cand.path.exists()


def test_a_failed_readback_removes_the_partial_remote_artifact(tmp_path):
    # "Space consumed, nothing accomplished" is the failure this card
    # names; leaving a partial artifact repeats it at the far end.
    # Arrange
    cand = _tree(tmp_path)
    fake = FakeRun(
        {
            "test -e": (1, ""),
            "df -Pk": (0, _df(free_kb=10_000_000)),
            "sha256sum": (1, ""),
        }
    )

    # Act
    _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert fake.ran("rm -f")


def test_a_truncated_remote_hash_is_rejected_rather_than_compared(tmp_path):
    # A short or garbled line must not be compared as though it were a
    # digest -- returning it would make equality depend on an error's shape.
    # Arrange
    fake = FakeRun({"sha256sum": (0, "deadbeef  /share/dest/x\n")})

    # Act
    result = remote_sha256("nas2", "/share/dest/x", runner=fake)

    # Assert
    assert result is None


def test_an_empty_source_tree_is_refused(tmp_path):
    # Counted BEFORE the stream. With remove_source=True the tree would be
    # gone by the time a post-hoc count ran, and a count of a deleted
    # directory is 0 -- which reads as success.
    # Arrange
    empty = tmp_path / "empty"
    empty.mkdir()
    cand = SweepCandidate(
        name="empty", path=empty, file_count=0, size=0, newest_mtime=0.0
    )
    fake = FakeRun({"test -e": (1, ""), "df -Pk": (0, _df(free_kb=10_000_000))})

    # Act
    err = _raised(
        lambda: stream_sweep_to_remote(
            cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn()
        )
    )

    # Assert
    assert "0 files" in str(err)


# --- the hash compared IS the hash of what was sent ---------------------
def test_the_streamed_bytes_are_hashed_in_flight(tmp_path):
    # POSITIVE CONTROL on the verification itself. If the digest were
    # computed from anything other than the streamed bytes -- a re-read, a
    # stale buffer -- every mismatch test above would pass for the WRONG
    # reason. Here the remote is made to agree with what was actually sent,
    # so the run must succeed and report that same digest.
    # Arrange
    cand = _tree(tmp_path)
    expected = _real_tar_digest(cand)
    fake = EchoRun(
        {"test -e": (1, ""), "df -Pk": (0, _df(free_kb=10_000_000))},
        {"digest": expected},
    )

    # Act
    result = stream_sweep_to_remote(
        cand, "nas2", "/share/dest", runner=fake, spawn=FakeSpawn(real_tar=True)
    )

    # Assert
    assert result.sha256 == expected


def test_a_VERIFIED_stream_does_remove_the_source_when_asked(tmp_path):
    # POSITIVE CONTROL ON THE REMOVAL PATH, and the reason it must exist:
    # "the source survives a mismatch" passes trivially if removal never
    # happens under ANY condition. Without this, every safety test above
    # would be satisfied by a `remove_source` flag that does nothing -- a
    # guard proving only that a dead code path is dead.
    # Arrange
    cand = _tree(tmp_path)
    fake = EchoRun(
        {"test -e": (1, ""), "df -Pk": (0, _df(free_kb=10_000_000))},
        {"digest": _real_tar_digest(cand)},
    )

    # Act
    stream_sweep_to_remote(
        cand,
        "nas2",
        "/share/dest",
        remove_source=True,
        runner=fake,
        spawn=FakeSpawn(real_tar=True),
    )

    # Assert
    assert not cand.path.exists()

# EOF
