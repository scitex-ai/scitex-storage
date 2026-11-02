"""Additive local content versions; Git owns pointer history, Storage owns bytes."""

from __future__ import annotations

from ._chunking import ChunkingConfig, NativeChunkerUnavailable
from ._manifest import ChunkRef, VersionManifest
from ._objects import IntegrityError, SourceChangedError
from ._pointer import (
    GitPointer,
    read_git_pointer,
    render_git_pointer,
    write_git_pointer,
)
from ._store import IngestResult, VersionStore

__all__ = [
    "ChunkingConfig",
    "NativeChunkerUnavailable",
    "ChunkRef",
    "VersionManifest",
    "IntegrityError",
    "SourceChangedError",
    "GitPointer",
    "read_git_pointer",
    "render_git_pointer",
    "write_git_pointer",
    "IngestResult",
    "VersionStore",
]
