"""Unit tests for scitex_storage._archive (move-not-delete nas/nas2 tiering).

A fake ``runner`` (matching scitex_ssh's own ``subprocess.run``-shaped
testing seam -- verified against its real call convention:
``runner(cmd, capture_output=True, text=True)`` returning an object with
``.returncode``/``.stdout``/``.stderr``) stands in for real ssh/rsync, so
no network or real SSH config is needed to exercise these code paths.
"""

import json
import os
from dataclasses import dataclass

import pytest

import shutil

from scitex_storage._transfer._archive import (
    DEFAULT_REMOTE_ROOT,
    ArchiveManifest,
    ArchiveNotVerifiedError,
    ArchivePlan,
    RestorePlan,
    _as_dir_contents,
    _quote_remote_path,
    _rsync_binary,
    apply_archive,
    apply_restore,
    plan_archive,
    plan_restore,
)
from scitex_storage._measure._scan import MissingSystemDependencyError
from scitex_storage._transfer._sweep import InsufficientSpaceError
from scitex_storage._transfer._verify import local_tally

# Resolved at collection time -- BEFORE any test's isolated_path_bin_dir
# fixture swaps PATH out from under a later real `shutil.which()` call.
_REAL_RSYNC_BIN = shutil.which("rsync")


@dataclass
class _FakeCompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _rsync_call(runner):
    """The recorded rsync argv, located by CONTENT rather than by position.

    These assertions used to read ``runner.calls[-1]``, which was true only
    while rsync happened to be the last thing apply_archive did. It is not
    any more: the destination read-back probe runs after it. An assertion
    that depends on incidental ordering breaks on an unrelated change and
    says nothing about what it meant to check, so it is pinned to the call
    that actually is rsync.
    """
    # Matched on argv[0] being the rsync BINARY, not on "rsync" appearing
    # anywhere in the argv: the ssh calls carry a remote path that can
    # contain the substring, and a loose match silently picks the wrong
    # call and then asserts against the wrong argv.
    for cmd in reversed(runner.calls):
        if cmd and str(cmd[0]).rsplit("/", 1)[-1] == "rsync":
            return cmd
    raise AssertionError(f"no rsync call recorded; got {runner.calls!r}")


def _looks_like_tally(cmd) -> bool:
    """True when ``cmd`` is apply_archive's destination read-back probe."""
    joined = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    return "! -type d" in joined and "wc -c" in joined


def _simulated_destination_tally(calls) -> str:
    """Answer the read-back probe as a destination that received EVERYTHING.

    ``apply_archive`` now reads the destination back before removing the
    source, so a runner that stays silent is an INCOMPLETE double, not a
    passing one -- it reads as "the probe did not run", which correctly
    refuses to delete. Rather than teach every call site about the probe,
    the double reports the real tally of the tree rsync was asked to send.

    The source is taken from the RECORDED RSYNC ARGV, not reconstructed
    from the remote path. The first attempt did the latter -- inverting
    DEFAULT_REMOTE_ROOT -- and broke the moment a test passed an explicit
    ``remote_path``, because that convention only holds for the default.
    The rsync argv carries the source directly and holds in every case.

    This models the honest success case. The FAILURE cases -- short count,
    surplus count, truncated bytes, unanswerable probe -- live at the unit
    level in test__verify.py, where they can be stated exactly instead of
    being simulated through a transport.
    """
    for cmd in reversed(calls):
        if cmd and str(cmd[0]).rsplit("/", 1)[-1] == "rsync" and len(cmd) >= 2:
            tally = local_tally(str(cmd[-2]).rstrip("/"))
            if tally.entry_count is None:
                return ""
            return f"{tally.entry_count}\n{tally.size_bytes}\n"
    return ""


class _FakeRunner:
    """Records every argv it's called with; returns the SAME scripted result
    for every call (mkdir and rsync alike -- use _StagedRunner to make only
    one of them fail).

    EXCEPT the destination read-back probe, which is answered as a
    destination that faithfully received the source -- see
    :func:`_simulated_destination_tally`. A scripted FAILURE
    (``returncode != 0``) is still honoured for the probe too, so the
    "transport is broken" tests keep working unchanged."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.returncode == 0 and _looks_like_tally(cmd):
            return _FakeCompletedProcess(0, _simulated_destination_tally(self.calls), "")
        return _FakeCompletedProcess(self.returncode, self.stdout, self.stderr)


def _looks_like_digest(cmd) -> bool:
    """True when ``cmd`` is apply_archive's CONTENT read-back probe."""
    joined = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    return "sha256sum" in joined


def _simulated_destination_digest(calls, corrupt: bool = False) -> str:
    """Answer the digest probe as a destination that received EVERYTHING.

    Built from the RECORDED RSYNC ARGV for the same reason the tally double
    is: the rsync argv carries the source directly, while inverting the
    remote path only holds for the default convention.

    ``corrupt=True`` returns the same PATHS with a wrong hash for one entry --
    the same-length/wrong-content case, which is the only thing this gate
    exists to catch and therefore the only failure worth simulating here.
    """
    from scitex_storage._transfer._content_verify import digest_tree

    for cmd in reversed(calls):
        if cmd and str(cmd[0]).rsplit("/", 1)[-1] == "rsync" and len(cmd) >= 2:
            manifest = digest_tree(str(cmd[-2]).rstrip("/"))
            lines = []
            for i, (rel, digest) in enumerate(sorted(manifest.digests.items())):
                if corrupt and i == 0:
                    digest = "0" * 64
                lines.append(f"{digest} ./{rel}")
            return "\n".join(lines) + "\n" if lines else ""
    return ""


class _FaithfulDigestRunner(_FakeRunner):
    """A destination that received the bytes faithfully, hashes and all."""

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if _looks_like_digest(cmd):
            return _FakeCompletedProcess(0, _simulated_destination_digest(self.calls), "")
        if _looks_like_tally(cmd):
            return _FakeCompletedProcess(0, _simulated_destination_tally(self.calls), "")
        return _FakeCompletedProcess(0, "", "")


class _CorruptDigestRunner(_FakeRunner):
    """Right count, right bytes, WRONG CONTENT -- the case the tally cannot see."""

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if _looks_like_digest(cmd):
            return _FakeCompletedProcess(
                0, _simulated_destination_digest(self.calls, corrupt=True), ""
            )
        if _looks_like_tally(cmd):
            return _FakeCompletedProcess(0, _simulated_destination_tally(self.calls), "")
        return _FakeCompletedProcess(0, "", "")


class _LyingTallyRunner:
    """Succeeds at mkdir and rsync, then reports a destination tally of
    ``tally`` -- used to prove the read-back actually REFUSES, rather than
    only proving the happy path still passes. A guard that has never been
    seen to fire is indistinguishable from one that cannot."""

    def __init__(self, tally: str):
        self.tally = tally
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if _looks_like_tally(cmd):
            return _FakeCompletedProcess(0, self.tally, "")
        return _FakeCompletedProcess(0, "", "")


class _FullDestinationRunner:
    """Succeeds at everything except `df`, which reports a destination with
    ``avail_kb`` 1-KiB blocks free -- so the preflight can be seen to
    REFUSE. Without this, every test would hit the could-not-look branch
    (the plain fake returns no df output at all) and the guard would never
    be observed firing."""

    def __init__(self, avail_kb: int = 0):
        self.avail_kb = avail_kb
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        joined = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "df -Pk" in joined:
            return _FakeCompletedProcess(
                0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                f"/dev/sda1 1000000 999999 {self.avail_kb} 100% /share\n",
                "",
            )
        if _looks_like_tally(cmd):
            return _FakeCompletedProcess(0, _simulated_destination_tally(self.calls), "")
        return _FakeCompletedProcess(0, "", "")


class _StagedRunner:
    """Scripts a distinct result per call index (1-based) -- e.g. mkdir
    (call 1) succeeds, rsync (call 2) fails. Any call beyond the scripted
    stages reuses the last stage's result."""

    def __init__(self, *stages):
        self.stages = stages
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        stage = self.stages[min(len(self.calls), len(self.stages)) - 1]
        return _FakeCompletedProcess(*stage)


def _touch(path, size=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def sandbox_home(tmp_path):
    """Real temp HOME so manifest writes never touch the real ~/.scitex."""
    home = tmp_path / "home"
    home.mkdir()
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev


# --- _quote_remote_path -------------------------------------------------------
# Tested directly: a regression in this exact helper (naive shlex.quote of
# a leading "~") silently broke real archives on nas2 -- see the module
# docstring's "Never wrap a leading ~ in shell quotes" bullet.


def test_quote_remote_path_leaves_bare_tilde_unquoted():
    # Arrange
    # Act
    quoted = _quote_remote_path("~")
    # Assert
    assert quoted == "~"


def test_quote_remote_path_leaves_tilde_slash_prefix_unquoted():
    # Arrange
    # Act
    quoted = _quote_remote_path("~/scitex-storage-archive")
    # Assert
    assert quoted.startswith("~/")


def test_quote_remote_path_quotes_the_rest_after_the_tilde():
    # Arrange
    # Act
    quoted = _quote_remote_path("~/a dir/b")
    # Assert
    assert quoted == "~/" + "'a dir/b'"


def test_quote_remote_path_quotes_a_non_tilde_path_normally():
    # Arrange
    # Act
    quoted = _quote_remote_path("/a dir/b")
    # Assert
    assert quoted == "'/a dir/b'"


# --- _as_dir_contents ----------------------------------------------------------
# Tested directly: rsync's trailing-slash "contents of" vs "the directory
# itself" distinction is exactly what silently misplaced restored data --
# see the module docstring's trailing-slash bullet.


def test_as_dir_contents_appends_a_trailing_slash():
    # Arrange
    # Act
    result = _as_dir_contents("/a/b")
    # Assert
    assert result == "/a/b/"


def test_as_dir_contents_is_idempotent_on_an_already_slashed_path():
    # Arrange
    # Act
    result = _as_dir_contents("/a/b/")
    # Assert
    assert result == "/a/b/"


def test_as_dir_contents_preserves_a_leading_tilde():
    # Arrange
    # Act
    result = _as_dir_contents("~/x")
    # Assert
    assert result == "~/x/"


# --- plan_archive -------------------------------------------------------------


def test_plan_archive_raises_for_unknown_destination(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    # Assert
    with pytest.raises(ValueError):
        plan_archive(source, "not-a-real-destination")


def test_plan_archive_raises_for_missing_source(tmp_path, sandbox_home):
    # Arrange
    missing = tmp_path / "does-not-exist"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_archive(missing, "nas")


def test_plan_archive_raises_for_non_directory_source(tmp_path, sandbox_home):
    # Arrange
    a_file = _touch(tmp_path / "a.bin")
    # Act
    # Assert
    with pytest.raises(NotADirectoryError):
        plan_archive(a_file, "nas")


def test_plan_archive_computes_size_bytes(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin", size=10)
    _touch(source / "b.bin", size=20)
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.size_bytes == 30


def test_plan_archive_computes_file_count(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    _touch(source / "b.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.file_count == 2


def test_plan_archive_default_remote_path_mirrors_source(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert plan.remote_path == f"~/scitex-storage-archive{source}"


def test_plan_archive_explicit_remote_path_overrides_default(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas", remote_path="~/custom/target")
    # Assert
    assert plan.remote_path == "~/custom/target"


def test_plan_archive_refuses_an_unsafe_remote_path(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    # Assert
    with pytest.raises(ValueError):
        plan_archive(source, "nas", remote_path="/")


def test_plan_archive_does_not_touch_the_source(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    # Act
    plan_archive(source, "nas")
    # Assert
    assert f.exists()


def test_plan_archive_returns_an_archiveplan_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan = plan_archive(source, "nas")
    # Assert
    assert isinstance(plan, ArchivePlan)


def test_plan_archive_manifest_path_is_deterministic(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    # Act
    plan_a = plan_archive(source, "nas")
    plan_b = plan_archive(source, "nas")
    # Assert
    assert plan_a.manifest_path == plan_b.manifest_path


# --- apply_archive --------------------------------------------------------------


def test_apply_archive_removes_the_source_on_success(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert not source.exists()


def test_apply_archive_writes_a_manifest_file_on_success(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert plan.manifest_path.exists()


def test_apply_archive_manifest_records_the_source_path(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert manifest.source == str(source)


def test_apply_archive_records_that_the_check_was_a_tally(tmp_path, sandbox_home):
    """The manifest must say WHAT the check could see, not only that it passed.

    `verified: "verified"` is a claim about the outcome. This field is the
    claim about the INSTRUMENT, and the two are not the same: the tally
    cannot see a destination file with the right name, the right length and
    the wrong bytes -- which `test__content_verify` asserts in CI -- and
    `apply_archive` deletes the source on that verdict.
    """
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert manifest.verification_method == "tally"


def test_the_manifest_file_on_disk_carries_the_method(tmp_path, sandbox_home):
    """It has to be in the ARTEFACT, not only on the returned object.

    A consumer reads the JSON; nobody re-runs the archive to ask Python.
    """
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert json.loads(plan.manifest_path.read_text())["verification_method"] == "tally"


def test_an_old_manifest_reads_back_as_unknown_not_tally():
    """Do not back-fill a claim an old artefact never made.

    Manifests written before this field existed were also tally-verified --
    but that is an inference about them, not something they recorded, and
    stamping it on would manufacture evidence. An old manifest genuinely does
    not say.
    """
    # Arrange
    old = {
        "source": "/x",
        "destination": "nas",
        "remote_path": "/y",
        "size_bytes": 1,
        "file_count": 1,
        "checksummed": True,
        "archived_at": 0.0,
        "verified": "verified",
        "verification_evidence": "…",
    }
    # Act
    manifest = ArchiveManifest.from_dict(old)
    # Assert
    assert manifest.verification_method == "unknown"


def test_the_content_gate_is_off_by_default(tmp_path, sandbox_home):
    """Opt-in, because rsync --checksum already reads every byte on both sides.

    Hashing both trees again by default would roughly double the cost of a
    multi-terabyte archive to close a narrower residual window, and a default
    nobody can afford to leave on gets turned off.
    """
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FaithfulDigestRunner(returncode=0))
    # Assert
    assert manifest.verification_method == "tally"


def test_the_content_gate_records_itself_when_asked_for(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(
        plan, verify_content_too=True, runner=_FaithfulDigestRunner(returncode=0)
    )
    # Assert
    assert manifest.verification_method == "content"


def test_a_faithful_destination_still_passes_the_content_gate(tmp_path, sandbox_home):
    """POSITIVE CONTROL: a gate that never passes is a gate nobody can use."""
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    apply_archive(
        plan, verify_content_too=True, runner=_FaithfulDigestRunner(returncode=0)
    )
    # Assert
    assert not source.exists()


@pytest.fixture
def refused_on_content(tmp_path, sandbox_home):
    """Archive against a destination whose CONTENT is wrong; capture the refusal.

    A fixture rather than repeated setup, so each test below carries exactly
    one assertion and a failure names the behaviour that broke.
    """
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    raised = None
    try:
        apply_archive(
            plan, verify_content_too=True, runner=_CorruptDigestRunner(returncode=0)
        )
    except ArchiveNotVerifiedError as exc:
        raised = exc
    return source, raised


def test_wrong_content_at_the_destination_refuses_the_delete(refused_on_content):
    """The whole reason the gate exists: the tally passes, this does not."""
    # Arrange
    _source, raised = refused_on_content
    # Act
    refused = isinstance(raised, ArchiveNotVerifiedError)
    # Assert
    assert refused is True


def test_wrong_content_leaves_the_source_intact(refused_on_content):
    # Arrange
    source, _raised = refused_on_content
    # Act
    survived = source.exists()
    # Assert
    assert survived is True


def test_the_same_destination_passes_the_tally_it_fails_on_content(tmp_path, sandbox_home):
    """DISCRIMINATING CONTROL — proves the gate adds something.

    Same runner, same destination, only the flag differs. If this ever fails
    because the tally ALSO catches it, the corruption being simulated is not
    this gate's class and the test above has stopped being evidence.
    """
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_CorruptDigestRunner(returncode=0))
    # Assert
    assert manifest.verified == "verified"


def test_apply_archive_creates_the_remote_parent_directory_first(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert
    assert "mkdir -p" in runner.calls[0][-1]


def test_apply_archive_mkdir_command_leaves_the_leading_tilde_unquoted(
    tmp_path, sandbox_home
):
    # Arrange -- a naive shlex.quote(whole_path) would wrap "~" in quotes,
    # which stops the remote shell from tilde-expanding it (regression:
    # found by scitex-ssh smoke-testing a real mkdir on nas2).
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert
    assert "'~" not in runner.calls[0][-1]


def test_apply_archive_rsync_source_has_a_trailing_slash(tmp_path, sandbox_home):
    # Arrange -- without the trailing slash, rsync nests source itself one
    # level deeper on the remote side instead of copying its contents
    # directly into remote_path (regression: found by scitex-ssh diffing
    # actual restored file paths, not just checksums, on a real round-trip).
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert -- the rsync call has src as the second-to-last argv
    assert _rsync_call(runner)[-2] == f"{plan.source}/"


def test_apply_archive_mkdir_runs_before_the_rsync_call(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, runner=runner)
    # Assert -- mkdir first, then rsync. (A third call, the destination
    # read-back probe, now follows both; this test is about the ORDER of
    # the first two, not the total.)
    assert runner.calls.index(_rsync_call(runner)) > 0


# A uniform-failure runner fails BOTH the mkdir and the rsync call, so it
# exercises the mkdir failure path specifically (mkdir is call 1, and the
# code raises before ever reaching the rsync call).


def test_apply_archive_leaves_source_untouched_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    try:
        apply_archive(plan, runner=_FakeRunner(returncode=1, stderr="mkdir error"))
    except RuntimeError:
        pass
    # Assert
    assert f.exists()


def test_apply_archive_raises_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_archive(plan, runner=_FakeRunner(returncode=1, stderr="mkdir error"))


def test_apply_archive_does_not_write_manifest_on_mkdir_failure(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    try:
        apply_archive(plan, runner=_FakeRunner(returncode=1))
    except RuntimeError:
        pass
    # Assert
    assert not plan.manifest_path.exists()


# A staged runner lets mkdir succeed so the rsync call specifically fails.


def test_apply_archive_leaves_source_untouched_on_sync_failure_after_mkdir_ok(
    tmp_path, sandbox_home
):
    # Arrange
    source = tmp_path / "source"
    f = _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _StagedRunner((0, "", ""), (1, "", "rsync error"))
    # Act
    try:
        apply_archive(plan, runner=runner)
    except RuntimeError:
        pass
    # Assert
    assert f.exists()


def test_apply_archive_raises_on_sync_failure_after_mkdir_ok(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _StagedRunner((0, "", ""), (1, "", "rsync error"))
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_archive(plan, runner=runner)


def test_apply_archive_checksum_true_adds_the_rsync_flag(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, checksum=True, runner=runner)
    # Assert
    assert "--checksum" in _rsync_call(runner)


def test_apply_archive_checksum_false_omits_the_rsync_flag(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, checksum=False, runner=runner)
    # Assert
    assert "--checksum" not in _rsync_call(runner)


def test_apply_archive_passes_exclude_patterns_through(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_archive(plan, exclude=("*.tmp",), runner=runner)
    # Assert
    assert "--exclude=*.tmp" in _rsync_call(runner)


def test_apply_archive_returns_an_archivemanifest_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    plan = plan_archive(source, "nas")
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert isinstance(manifest, ArchiveManifest)


# --- plan_restore -------------------------------------------------------------


def test_plan_restore_raises_when_no_manifest_exists(tmp_path, sandbox_home):
    # Arrange
    never_archived = tmp_path / "never-archived"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        plan_restore(never_archived)


def test_plan_restore_loads_a_previously_written_manifest(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    archive_plan = plan_archive(source, "nas")
    apply_archive(archive_plan, runner=_FakeRunner(returncode=0))
    # Act
    restore_plan = plan_restore(source)
    # Assert
    assert restore_plan.manifest.source == str(source)


def test_plan_restore_returns_a_restoreplan_instance(tmp_path, sandbox_home):
    # Arrange
    source = tmp_path / "source"
    _touch(source / "a.bin")
    archive_plan = plan_archive(source, "nas")
    apply_archive(archive_plan, runner=_FakeRunner(returncode=0))
    # Act
    restore_plan = plan_restore(source)
    # Assert
    assert isinstance(restore_plan, RestorePlan)


# --- apply_restore -------------------------------------------------------------


def _restore_plan(source, destination="nas", remote_path="~/archive/x"):
    manifest = ArchiveManifest(
        source=str(source),
        destination=destination,
        remote_path=remote_path,
        size_bytes=10,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
    )
    return RestorePlan(manifest=manifest, manifest_path=source.parent / "unused.json")


def test_apply_restore_returns_the_source_path_on_success(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    # Act
    result = apply_restore(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert result == source


def test_apply_restore_rsync_source_has_a_trailing_slash(tmp_path):
    # Arrange -- without the trailing slash, rsync nests remote_path itself
    # one level deeper under the local source instead of copying its
    # contents directly into it (same bug class as the archive-side push,
    # found by scitex-ssh diffing actual restored file paths).
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="~/archive/x")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, runner=runner)
    # Assert -- pull's src is "host:remote_path", the second-to-last argv
    assert runner.calls[-1][-2] == "nas:~/archive/x/"


def test_apply_restore_raises_on_pull_failure(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_restore(plan, runner=_FakeRunner(returncode=1, stderr="rsync error"))


def test_apply_restore_without_delete_remote_makes_only_one_call(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=False, runner=runner)
    # Assert
    assert len(runner.calls) == 1


def test_apply_restore_with_delete_remote_makes_a_second_call(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=True, runner=runner)
    # Assert
    assert len(runner.calls) == 2


def test_apply_restore_delete_remote_command_targets_the_remote_path(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="~/archive/needle")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=True, runner=runner)
    # Assert
    assert "needle" in runner.calls[1][-1]


def test_apply_restore_delete_remote_command_leaves_the_leading_tilde_unquoted(tmp_path):
    # Arrange -- same regression class as the archive-side mkdir: a naive
    # shlex.quote(whole_path) would wrap "~" and break tilde-expansion.
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="~/archive/needle")
    runner = _FakeRunner(returncode=0)
    # Act
    apply_restore(plan, delete_remote=True, runner=runner)
    # Assert
    assert "'~" not in runner.calls[1][-1]


def test_apply_restore_raises_if_remote_delete_fails(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source)

    class _TwoStageRunner:
        def __init__(self):
            self.n = 0

        def __call__(self, cmd, **kwargs):
            self.n += 1
            # First call (sync_dir pull) succeeds; second (exec_remote rm) fails.
            return _FakeCompletedProcess(0 if self.n == 1 else 1, "", "rm failed")

    # Act
    # Assert
    with pytest.raises(RuntimeError):
        apply_restore(plan, delete_remote=True, runner=_TwoStageRunner())


def test_apply_restore_refuses_to_delete_an_unsafe_remote_path(tmp_path):
    # Arrange
    source = tmp_path / "source"
    plan = _restore_plan(source, remote_path="/")
    # Act
    # Assert
    with pytest.raises(ValueError):
        apply_restore(plan, delete_remote=True, runner=_FakeRunner(returncode=0))


# =============================================================================
# rsync dependency -- missing-binary error handling
#
# `archive`/`restore` never spawn rsync themselves: they call scitex-ssh's
# `sync_dir`, which is a wrapper over `rsync -a` over ssh. That makes the
# LOCAL rsync binary a hard runtime dependency that is INVISIBLE from this
# package's source -- the subprocess happens one package away. It was missed
# until 2026-07-17, when `archive` turned out not to run in the container
# scitex-storage ships to.
#
# Same no-mocks approach as `_scan.py`'s fd tests (STX-NM002 -- "production
# talks to the real collaborator"): PATH is isolated via a real env-var
# mutation, so the code under test still calls the real `shutil.which`.
# =============================================================================


@pytest.fixture
def isolated_path_bin_dir(tmp_path):
    """Replace PATH with a fresh, empty directory for the test's duration."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    original_path = os.environ["PATH"]
    os.environ["PATH"] = str(bin_dir)
    yield bin_dir
    os.environ["PATH"] = original_path


def test_rsync_binary_raises_missing_dependency_when_rsync_absent(
    isolated_path_bin_dir,
):
    # Arrange -- PATH now contains no rsync.
    # Act
    # Assert
    with pytest.raises(MissingSystemDependencyError):
        _rsync_binary()


def test_rsync_missing_dependency_names_an_actual_install_command(
    isolated_path_bin_dir,
):
    # Arrange -- an error a user cannot act on is only half a fail-loud.
    # Act
    # Assert
    with pytest.raises(MissingSystemDependencyError, match="apt install rsync"):
        _rsync_binary()


@pytest.mark.skipif(
    _REAL_RSYNC_BIN is None, reason="requires a real `rsync` binary on PATH"
)
def test_rsync_binary_returns_the_real_binary_when_present():
    # Arrange
    # Act
    found = _rsync_binary()
    # Assert
    assert found == _REAL_RSYNC_BIN


def test_plan_archive_does_not_require_rsync(tmp_path, isolated_path_bin_dir):
    # Arrange -- planning is genuinely transport-free, so it must not demand
    # a binary it will never invoke. (`scan`'s fd IS still needed for the
    # size walk, so this asserts only that the failure is not rsync's.)
    source = tmp_path / "source"
    source.mkdir()
    # Act
    # Assert
    try:
        plan_archive(source, "nas2")
    except MissingSystemDependencyError as exc:
        assert "rsync" not in str(exc)


def test_apply_archive_with_an_injected_runner_does_not_require_rsync(
    tmp_path, sandbox_home, isolated_path_bin_dir
):
    # Arrange -- an injected runner IS the transport, so requiring a binary
    # it will never invoke would break the seam scitex-ssh deliberately
    # exposes. PATH has no rsync; this must still succeed.
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.bin").write_bytes(b"\0")
    plan = ArchivePlan(
        source=source,
        destination="nas2",
        remote_path="~/scitex-storage-archive/probe",
        size_bytes=1,
        file_count=1,
        manifest_path=sandbox_home / "manifest.json",
    )
    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))
    # Assert
    assert manifest.destination == "nas2"


# --- the destination read-back actually refuses ---------------------------
# These exist because a guard that has never been OBSERVED to fire is
# indistinguishable from one that cannot. The happy-path tests above prove
# the verb still works; only these prove it still protects anything.
def _plan_with_two_files(tmp_path, sandbox_home):
    source = tmp_path / "source"
    _touch(source / "a.bin")
    _touch(source / "b.bin")
    return plan_archive(source, "nas")


def test_a_short_destination_tally_refuses_and_raises(tmp_path, sandbox_home):
    # Arrange -- destination reports 1 entry where the source has 2.
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    raised = pytest.raises(ArchiveNotVerifiedError)

    # Assert
    with raised:
        apply_archive(plan, runner=_LyingTallyRunner("1\n999999\n"))


def test_a_short_destination_tally_LEAVES_THE_SOURCE_INTACT(tmp_path, sandbox_home):
    # The consequence that matters: refusing is worthless if it deleted first.
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    try:
        apply_archive(plan, runner=_LyingTallyRunner("1\n999999\n"))
    except ArchiveNotVerifiedError:
        pass

    # Assert
    assert plan.source.exists()


def test_an_unanswerable_probe_LEAVES_THE_SOURCE_INTACT(tmp_path, sandbox_home):
    # "I could not check" must block the delete exactly as firmly as
    # "the check failed" -- for a destructive action they have the same
    # consequence, and treating unknown as permission is the whole defect.
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    try:
        apply_archive(plan, runner=_LyingTallyRunner(""))
    except ArchiveNotVerifiedError:
        pass

    # Assert
    assert plan.source.exists()


def test_a_refused_archive_still_records_the_verdict_in_the_manifest(
    tmp_path, sandbox_home
):
    # A refusal that lives only in the raiser's terminal is not auditable.
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    try:
        apply_archive(plan, runner=_LyingTallyRunner("1\n999999\n"))
    except ArchiveNotVerifiedError:
        pass

    # Assert
    assert "mismatch" in plan.manifest_path.read_text()


def test_a_full_destination_refuses_before_transferring(tmp_path, sandbox_home):
    # The sweep lesson applied to archive: a verb that moves data OFF a
    # full filesystem must measure the destination it is moving TO.
    # Arrange -- destination reports zero blocks free.
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    raised = pytest.raises(InsufficientSpaceError)

    # Assert
    with raised:
        apply_archive(plan, runner=_FullDestinationRunner(avail_kb=0))


def test_a_full_destination_LEAVES_THE_SOURCE_INTACT(tmp_path, sandbox_home):
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    try:
        apply_archive(plan, runner=_FullDestinationRunner(avail_kb=0))
    except InsufficientSpaceError:
        pass

    # Assert
    assert plan.source.exists()


def test_a_full_destination_TRANSFERS_NOTHING(tmp_path, sandbox_home):
    # Refusing after pushing the data would defeat the point: the preflight
    # exists to avoid filling a destination, not to report it afterwards.
    # Arrange
    runner = _FullDestinationRunner(avail_kb=0)
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    try:
        apply_archive(plan, runner=runner)
    except InsufficientSpaceError:
        pass

    # Assert -- no rsync was ever invoked
    assert not any(
        cmd and str(cmd[0]).rsplit("/", 1)[-1] == "rsync" for cmd in runner.calls
    )


def test_a_roomy_destination_proceeds(tmp_path, sandbox_home):
    # The guard must not block the ordinary case: 1 TiB free.
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    manifest = apply_archive(plan, runner=_FullDestinationRunner(avail_kb=1073741824))

    # Assert
    assert manifest.verified == "verified"


def test_a_verified_archive_records_verified_in_the_manifest(tmp_path, sandbox_home):
    # Arrange
    plan = _plan_with_two_files(tmp_path, sandbox_home)

    # Act
    manifest = apply_archive(plan, runner=_FakeRunner(returncode=0))

    # Assert
    assert manifest.verified == "verified"


# EOF
