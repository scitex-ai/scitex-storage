"""Small ordinary Git files pointing to Storage-owned versions, not Git LFS."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .._transfer._durable import sync_directory
from ._manifest import VersionManifest, encode_record
from ._objects import IntegrityError, open_regular, validate_digest

POINTER_SCHEMA = "scitex-storage/git-pointer-v1"


@dataclass(frozen=True)
class GitPointer:
    """Metadata in a plain Git file, independent of machine paths."""

    version_id: str
    file_sha256: str
    size: int
    chunk_count: int

    def __post_init__(self):
        validate_digest(self.version_id)
        validate_digest(self.file_sha256)
        if any(
            type(value) is not int or value < 0
            for value in (self.size, self.chunk_count)
        ):
            raise IntegrityError("Invalid pointer size/count")


def render_git_pointer(manifest: VersionManifest) -> str:
    """Render reviewable pointer bytes; normal Git tools own edits/history."""
    GitPointer(
        manifest.version_id, manifest.file_sha256, manifest.size, manifest.chunk_count
    )
    return encode_record(
        {
            "schema": POINTER_SCHEMA,
            "version_sha256": manifest.version_id,
            "file_sha256": manifest.file_sha256,
            "size": manifest.size,
            "chunk_count": manifest.chunk_count,
        }
    ).decode("utf-8")


def read_git_pointer(path: str | Path) -> GitPointer:
    """Read a bounded, strict SciTeX pointer without following a symlink."""
    try:
        with open_regular(Path(path)) as stream:
            data = stream.read(4_097)
        value = json.loads(data)
    except (OSError, ValueError, UnicodeError) as exc:
        raise IntegrityError("Cannot read a SciTeX version pointer") from exc
    if (
        len(data) > 4_096
        or not isinstance(value, dict)
        or set(value)
        != {"schema", "version_sha256", "file_sha256", "size", "chunk_count"}
        or value.get("schema") != POINTER_SCHEMA
    ):
        raise IntegrityError("Expected a SciTeX pointer, not a Git LFS pointer")
    if any(
        type(value[key]) is not int or value[key] < 0 for key in ("size", "chunk_count")
    ):
        raise IntegrityError("Invalid pointer size/count")
    return GitPointer(
        validate_digest(value["version_sha256"]),
        validate_digest(value["file_sha256"]),
        value["size"],
        value["chunk_count"],
    )


def write_git_pointer(destination: str | Path, manifest: VersionManifest) -> Path:
    """Create a pointer atomically, never replacing an existing tracked file.

    To update a tracked pointer, render_git_pointer supplies bytes for the
    caller's normal reviewed Git editing workflow; this helper has no force
    mode and never runs Git, configures filters, or connects to a server.
    """
    requested = Path(destination).expanduser().absolute()
    target = requested.parent.resolve(strict=True) / requested.name
    if os.path.lexists(target):
        raise FileExistsError(f"Pointer creation never overwrites: {target}")
    descriptor, name = tempfile.mkstemp(prefix=".stx-pointer-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(render_git_pointer(manifest).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target, follow_symlinks=False)
        sync_directory(target.parent)
        return target
    finally:
        temporary.unlink(missing_ok=True)
