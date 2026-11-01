"""Additive local scientific-data snapshots with immutable chunk reuse."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .._transfer._durable import sync_directory
from ._chunking import ChunkingConfig, iter_chunks
from ._manifest import ChunkRef, VersionManifest, encode_record, header, walk_manifest
from ._objects import (
    IntegrityError,
    SourceChangedError,
    ensure_directory,
    object_path,
    open_regular,
    publish_file,
    put_bytes,
    signature,
)


@dataclass(frozen=True)
class IngestResult:
    """Measured payload reuse; excludes metadata and filesystem overhead."""

    manifest: VersionManifest
    new_chunks: int
    reused_chunks: int
    new_bytes: int
    reused_bytes: int


class VersionStore:
    """Local content-addressed objects; construction/read operations write nothing.

    Ingest never changes its source. Checkout creates a new verified file and
    never replaces an occupant. There is deliberately no deletion/GC method.
    """

    def __init__(self, root: str | Path, *, chunking: ChunkingConfig | None = None):
        requested = Path(root).expanduser().absolute()
        if requested.is_symlink():
            raise ValueError("The version store root must not be a symlink")
        self.root = requested.resolve()
        self.chunking = chunking or ChunkingConfig()
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("The version store root must be a directory")

    def _outside_store(self, path: Path):
        if path == self.root or self.root in path.parents:
            raise ValueError(
                "Sources and checkout destinations must be outside the object store"
            )

    def _source_info(self, source: str | Path):
        requested = Path(source).expanduser().absolute()
        before = requested.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(
                "Ingest requires a regular source, not a symlink/special file"
            )
        source_path = requested.resolve(strict=True)
        self._outside_store(source_path)
        return requested, source_path, before

    def plan_ingest(self, source: str | Path) -> dict:
        """Stat-only preview; no payload reads or filesystem writes."""
        requested, _, info = self._source_info(source)
        return {
            "status": "planned",
            "source": str(requested),
            "size": info.st_size,
            "store": str(self.root),
            "chunking": self.chunking.manifest_fields(),
            "new_bytes": None,
            "reused_bytes": None,
        }

    def ingest(
        self,
        source: str | Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> IngestResult:
        """Read a stable regular file, adding chunks and publishing a version last.

        A changing source leaves no committed version; already added immutable
        chunks may remain. No cleanup pass ever deletes existing objects.
        """
        requested, source_path, before = self._source_info(source)
        directory = ensure_directory(self.root, "versions")
        descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=directory)
        temporary = Path(name)
        manifest_hash, file_hash = hashlib.sha256(), hashlib.sha256()
        new = reused = new_bytes = reused_bytes = count = size = 0
        try:
            with (
                os.fdopen(descriptor, "wb") as manifest_stream,
                open_regular(source_path) as source_stream,
            ):
                if signature(os.fstat(source_stream.fileno())) != signature(before):
                    raise SourceChangedError("Source changed before reading")

                def write(record):
                    encoded = encode_record(record)
                    manifest_stream.write(encoded)
                    manifest_hash.update(encoded)

                write(header(self.chunking))
                for data in iter_chunks(source_stream, self.chunking):
                    file_hash.update(data)
                    digest, created = put_bytes(self.root, "chunks", data)
                    write({"type": "chunk", "sha256": digest, "size": len(data)})
                    count += 1
                    size += len(data)
                    if on_progress is not None:
                        on_progress(size, before.st_size)
                    if created:
                        new += 1
                        new_bytes += len(data)
                    else:
                        reused += 1
                        reused_bytes += len(data)
                try:
                    after = requested.stat(follow_symlinks=False)
                except OSError as exc:
                    raise SourceChangedError(
                        "Source pathname changed while reading"
                    ) from exc
                if (
                    signature(before) != signature(after)
                    or signature(before) != signature(os.fstat(source_stream.fileno()))
                    or size != before.st_size
                ):
                    raise SourceChangedError(
                        "Source changed while reading; no version published"
                    )
                write(
                    {
                        "type": "footer",
                        "sha256": file_hash.hexdigest(),
                        "size": size,
                        "chunk_count": count,
                    }
                )
                manifest_stream.flush()
                os.fchmod(manifest_stream.fileno(), 0o444)
                os.fsync(manifest_stream.fileno())
            version_id = manifest_hash.hexdigest()
            target_directory = ensure_directory(
                self.root, "versions", "sha256", version_id[:2]
            )
            publish_file(
                temporary,
                target_directory / version_id,
                version_id,
                temporary.stat().st_size,
            )
            summary = VersionManifest(
                version_id, file_hash.hexdigest(), size, count, self.chunking
            )
            return IngestResult(summary, new, reused, new_bytes, reused_bytes)
        finally:
            temporary.unlink(missing_ok=True)

    def read_manifest(self, version_id: str) -> VersionManifest:
        """Verify manifest bytes and its streaming structural invariants."""
        summary = None
        for kind, value in walk_manifest(
            object_path(self.root, "versions", version_id), version_id
        ):
            if kind == "footer":
                summary = value
        if not isinstance(summary, VersionManifest):
            raise IntegrityError("Version is incomplete")
        return summary

    def iter_chunks(self, version_id: str) -> Iterator[ChunkRef]:
        """Inspect references without allocating a list proportional to file size."""
        self.read_manifest(version_id)
        for kind, value in walk_manifest(
            object_path(self.root, "versions", version_id), version_id
        ):
            if kind == "chunk":
                yield value

    def checkout(
        self,
        version_id: str,
        destination: str | Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Publish a new file only after verifying each chunk and the full result."""
        requested = Path(destination).expanduser().absolute()
        parent = requested.parent.resolve(strict=True)
        target = parent / requested.name
        self._outside_store(target)
        if os.path.lexists(target):
            raise FileExistsError(
                f"Checkout never overwrites an existing path: {target}"
            )
        expected = self.read_manifest(version_id)
        if shutil.disk_usage(parent).free < expected.size:
            raise OSError("Insufficient destination free space for verified checkout")
        descriptor, name = tempfile.mkstemp(prefix=".stx-checkout-", dir=parent)
        temporary = Path(name)
        full_hash, total = hashlib.sha256(), 0
        summary = None
        try:
            with os.fdopen(descriptor, "wb") as output:
                for kind, value in walk_manifest(
                    object_path(self.root, "versions", version_id), version_id
                ):
                    if kind == "chunk":
                        ref = value
                        digest, chunk_size = hashlib.sha256(), 0
                        try:
                            with open_regular(
                                object_path(self.root, "chunks", ref.sha256)
                            ) as stream:
                                if os.fstat(stream.fileno()).st_size != ref.size:
                                    raise IntegrityError(
                                        "Chunk size differs from its manifest"
                                    )
                                for block in iter(
                                    lambda: stream.read(self.chunking.read_size), b""
                                ):
                                    if (
                                        chunk_size + len(block) > ref.size
                                        or total + len(block) > expected.size
                                    ):
                                        raise IntegrityError(
                                            "Immutable data grew while reading"
                                        )
                                    digest.update(block)
                                    full_hash.update(block)
                                    output.write(block)
                                    chunk_size += len(block)
                                    total += len(block)
                        except OSError as exc:
                            raise IntegrityError(
                                f"Missing/unreadable chunk: {ref.sha256}"
                            ) from exc
                        if chunk_size != ref.size or digest.hexdigest() != ref.sha256:
                            raise IntegrityError(
                                f"Chunk verification failed: {ref.sha256}"
                            )
                        if on_progress is not None:
                            on_progress(total, expected.size)
                    elif kind == "footer":
                        summary = value
                if (
                    not isinstance(summary, VersionManifest)
                    or total != summary.size
                    or full_hash.hexdigest() != summary.file_sha256
                ):
                    raise IntegrityError("Reconstructed file verification failed")
                output.flush()
                os.fsync(output.fileno())
            # Atomic no-replace, including a racing file or broken symlink.
            os.link(temporary, target, follow_symlinks=False)
            sync_directory(parent)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def checkout_pointer(
        self,
        pointer: str | Path,
        destination: str | Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Require pointer metadata to match the verified manifest before writing."""
        from ._pointer import read_git_pointer

        ref = read_git_pointer(pointer)
        manifest = self.read_manifest(ref.version_id)
        if (ref.file_sha256, ref.size, ref.chunk_count) != (
            manifest.file_sha256,
            manifest.size,
            manifest.chunk_count,
        ):
            raise IntegrityError("Pointer metadata differs from the verified manifest")
        return self.checkout(ref.version_id, destination, on_progress=on_progress)
