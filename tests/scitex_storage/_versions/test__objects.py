"""Immutable object reuse and rejection of corrupt/special/redirected files."""

import os

from scitex_storage._versions._objects import open_regular, put_bytes


def _refusal(action):
    try:
        action()
    except Exception as exc:
        return type(exc).__name__
    return None


def test_existing_verified_object_is_reused_without_rewriting(tmp_path):
    # Arrange
    # Act
    digest, created = put_bytes(tmp_path / "store", "chunks", b"payload")
    target = tmp_path / "store/chunks/sha256" / digest[:2] / digest
    before = target.stat()
    same, again = put_bytes(tmp_path / "store", "chunks", b"payload")
    # Assert
    assert (
        created,
        again,
        same,
        target.stat().st_mtime_ns,
        list(target.parent.glob(".pending-*")),
    ) == (True, False, digest, before.st_mtime_ns, [])


def test_corrupt_existing_object_is_never_overwritten(tmp_path):
    # Arrange
    # Act
    digest, _ = put_bytes(tmp_path, "chunks", b"payload")
    target = tmp_path / "chunks/sha256" / digest[:2] / digest
    target.chmod(0o600)
    target.write_bytes(b"bad")
    failure_1 = _refusal(lambda: put_bytes(tmp_path, "chunks", b"payload"))
    # Assert
    assert (failure_1, (target.read_bytes() == b"bad")) == ("IntegrityError", True)


def test_regular_reader_refuses_fifo_without_blocking(tmp_path):
    # Arrange
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    # Act
    failure_1 = _refusal(lambda: open_regular(fifo))
    # Assert
    assert (failure_1,) == ("IntegrityError",)


def test_symlink_object_cannot_redirect_to_external_payload(tmp_path):
    # Arrange
    # Act
    digest, _ = put_bytes(tmp_path / "objects", "chunks", b"payload")
    target = tmp_path / "objects/chunks/sha256" / digest[:2] / digest
    outside = tmp_path / "outside"
    outside.write_bytes(b"payload")
    target.unlink()
    target.symlink_to(outside)
    failure_1 = _refusal(lambda: put_bytes(tmp_path / "objects", "chunks", b"payload"))
    # Assert
    assert (failure_1, (outside.read_bytes() == b"payload")) == ("IntegrityError", True)
