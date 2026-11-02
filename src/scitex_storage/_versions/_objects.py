"""Immutable local object publication and bounded verified reads."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

from .._transfer._durable import sync_directory


class IntegrityError(ValueError):
    """An immutable object or manifest differs from its advertised digest."""


class SourceChangedError(RuntimeError):
    """The source changed while its snapshot was being ingested."""


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise IntegrityError("Expected a lowercase SHA256 digest")
    return value


def signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_regular(path: Path):
    """Reject symlinks/special files without blocking on a raced FIFO."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise IntegrityError("Only regular files are supported")
    return os.fdopen(descriptor, "rb")


def hash_file(path: Path, read_size=64 * 1_024) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    try:
        with open_regular(path) as stream:
            for block in iter(lambda: stream.read(read_size), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise IntegrityError(f"Cannot read immutable object: {path.name}") from exc
    return digest.hexdigest(), size


def _make_directory(path: Path):
    try:
        path.mkdir(mode=0o700)
    except FileNotFoundError:
        _make_directory(path.parent)
        _make_directory(path)
        return
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise IntegrityError("Object store directories must be real directories")
    else:
        sync_directory(path.parent)


def ensure_directory(root: Path, *parts: str) -> Path:
    """Create and persist internal directories without symlink redirection."""
    current = root
    for component in (None, *parts):
        if component is not None:
            current /= component
        _make_directory(current)
    return current


def object_path(root: Path, kind: str, digest: str) -> Path:
    validate_digest(digest)
    directory = root
    for component in (None, kind, "sha256", digest[:2]):
        if component is not None:
            directory /= component
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise IntegrityError(
                "Immutable object directories cannot redirect through symlinks"
            )
    return directory / digest


def publish_file(temporary: Path, destination: Path, digest: str, size: int) -> bool:
    """Link a completed same-filesystem object without replacing any occupant."""
    try:
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError:
        if hash_file(destination) != (digest, size):
            raise IntegrityError(f"Existing object is corrupt: {destination.name}")
        return False
    sync_directory(destination.parent)
    return True


def put_bytes(root: Path, kind: str, data: bytes) -> tuple[str, bool]:
    digest = hashlib.sha256(data).hexdigest()
    directory = ensure_directory(root, kind, "sha256", digest[:2])
    target = directory / digest
    if os.path.lexists(target):
        if hash_file(target) != (digest, len(data)):
            raise IntegrityError(f"Existing object is corrupt: {digest}")
        return digest, False
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        return digest, publish_file(temporary, directory / digest, digest, len(data))
    finally:
        temporary.unlink(missing_ok=True)
