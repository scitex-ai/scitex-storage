"""Real local rsync and shell read-back exercise mandatory archive integrity."""

import os
import subprocess
from pathlib import Path

import pytest

from scitex_storage._transfer._archive import (
    ArchiveNotVerifiedError,
    ArchivePlan,
    apply_archive,
)


class LocalTransport:
    """Run real transport commands locally, restricted to the owned temp tree.

    Only the SSH destination namespace is translated; commands, rsync, tally,
    sha256sum and exit statuses execute normally. No scripted probe replies.
    """

    def __init__(self, root, *, mutation=None):
        self.root = root
        self.mutation = mutation

    def __call__(self, command, **kwargs):
        if command[0] == "ssh":
            script = command[-1]
            if str(self.root) not in script:
                raise ValueError("Command escaped owned transport fixture")
            return subprocess.run(["sh", "-c", script], **kwargs)
        if command[0] == "scp":
            destination = Path(command[-1].split(":", 1)[1])
            source = Path(command[-2])
            if not all(
                path.is_relative_to(self.root) for path in (source, destination)
            ):
                raise ValueError("Sidecar copy escaped owned transport fixture")
            return subprocess.run(["cp", str(source), str(destination)], **kwargs)
        if Path(command[0]).name == "rsync":
            args = list(command)
            args[-1] = args[-1].split(":", 1)[1]
            if not Path(args[-1]).is_relative_to(self.root):
                raise ValueError("Destination escaped owned transport fixture")
            result = subprocess.run(args, **kwargs)
            if self.mutation:
                self.mutation()
            return result
        raise ValueError("Unsupported transport command")


@pytest.fixture
def archive_tree(tmp_path):
    """All source, destination, manifest and staging files stay in one temp root."""
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "paper.bin").write_bytes(b"original bytes")
    plan = ArchivePlan(
        source=source,
        destination="scitex-nas-03",
        remote_path=str(tmp_path / "remote" / "archive"),
        size_bytes=14,
        file_count=1,
        manifest_path=tmp_path / "manifest.json",
    )
    try:
        yield tmp_path, plan
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def test_default_archive_retires_only_independently_verified_content(archive_tree):
    # Arrange
    root, plan = archive_tree
    # Act
    manifest = apply_archive(plan, runner=LocalTransport(root))
    # Assert
    assert (
        manifest.verification_method,
        manifest.retained_source,
        Path(manifest.staged_path, "paper.bin").read_bytes(),
        (Path(plan.remote_path) / "paper.bin").read_bytes(),
    ) == ("content", True, b"original bytes", b"original bytes")


def test_corrupt_destination_cannot_retire_source_even_with_legacy_false_flag(
    archive_tree,
):
    # Arrange
    root, plan = archive_tree
    destination = Path(plan.remote_path) / "paper.bin"
    transport = LocalTransport(
        root, mutation=lambda: destination.write_bytes(b"corrupted byte")
    )
    # Act
    refused = False
    try:
        apply_archive(plan, verify_content_too=False, runner=transport)
    except ArchiveNotVerifiedError:
        refused = True
    # Assert
    assert (refused, (plan.source / "paper.bin").read_bytes()) == (
        True,
        b"original bytes",
    )


def test_changed_source_during_copy_is_not_retired(archive_tree):
    # Arrange
    root, plan = archive_tree
    transport = LocalTransport(
        root,
        mutation=lambda: (plan.source / "paper.bin").write_bytes(b"new source now"),
    )
    # Act
    refused = False
    try:
        apply_archive(plan, runner=transport)
    except ArchiveNotVerifiedError:
        refused = True
    # Assert
    assert (refused, (plan.source / "paper.bin").read_bytes()) == (
        True,
        b"new source now",
    )


def test_copy_only_archive_keeps_original_at_its_path(archive_tree):
    # Arrange
    root, plan = archive_tree
    # Act
    manifest = apply_archive(plan, remove_source=False, runner=LocalTransport(root))
    # Assert
    assert (
        manifest.retained_source,
        manifest.staged_path,
        (plan.source / "paper.bin").read_bytes(),
    ) == (True, "", b"original bytes")


def test_remote_move_cleanup_defers_without_fresh_bounded_target_verifier(archive_tree):
    # Arrange
    from scitex_storage._transfer._retention import sweep_restorable

    root, plan = archive_tree
    manifest = apply_archive(plan, runner=LocalTransport(root))
    # Act
    report = sweep_restorable(dry_run=False, now=manifest.archived_at + 8 * 86400)
    # Assert
    assert (report.removed, report.retained, Path(manifest.staged_path).exists()) == (
        [],
        [manifest.staged_path],
        True,
    )


def test_remote_callback_checks_real_destination_before_expired_cleanup(archive_tree):
    # Arrange
    import time

    from scitex_storage._transfer._retention import _bounded_snapshot, sweep_restorable

    root, plan = archive_tree
    manifest = apply_archive(plan, runner=LocalTransport(root))

    def verify(target, *, deadline, cancelled, max_bytes, max_entries):
        if time.monotonic() >= deadline or cancelled():
            return False
        return _bounded_snapshot(
            Path(target["path"]), max_entries, cancelled, max_bytes=max_bytes
        ) == (target["content_digest"], plan.size_bytes)

    # Act
    report = sweep_restorable(
        dry_run=False, now=manifest.archived_at + 8 * 86400, target_verifier=verify
    )
    # Assert
    assert (
        report.removed,
        Path(manifest.staged_path).exists(),
        (Path(plan.remote_path) / "paper.bin").read_bytes(),
    ) == ([manifest.staged_path], False, b"original bytes")
