"""Crash-consistent local state writes for transfer and retention records."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def atomic_json(path: Path, value) -> None:
    """Replace a JSON record only after its bytes are flushed to stable storage."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def file_lock(path: Path, *, blocking=True):
    """Serialize state transitions across processes without following lock symlinks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def rename_noreplace(source: Path, destination: Path):
    """Atomic Linux rename which cannot overwrite a racing destination."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NotImplementedError(
            "Safe retention requires renameat2 no-replace support"
        )
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise FileExistsError(number, os.strerror(number), str(destination))
        raise OSError(number, os.strerror(number), str(destination))


def sync_directory(path: Path):
    """Persist a completed rename or unlink before retiring its intent record."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
