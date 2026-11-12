"""Streaming, content-addressed version manifests (one bounded JSON record/line)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ._chunking import ALGORITHM, ChunkingConfig
from ._objects import IntegrityError, open_regular, validate_digest

SCHEMA = "scitex-storage/content-version-v1"
_MAX_RECORD = 4_096


@dataclass(frozen=True)
class ChunkRef:
    """One ordered immutable payload reference."""

    sha256: str
    size: int


@dataclass(frozen=True)
class VersionManifest:
    """Verified version summary; references remain streamed on disk."""

    version_id: str
    file_sha256: str
    size: int
    chunk_count: int
    chunking: ChunkingConfig


def encode_record(record: dict) -> bytes:
    return (
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()


def header(config: ChunkingConfig) -> dict:
    return {"type": "header", "schema": SCHEMA, "chunking": config.manifest_fields()}


def _integer(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise IntegrityError("Manifest sizes/counts must be nonnegative integers")
    return value


def walk_manifest(path: Path, version_id: str) -> Iterator[tuple[str, object]]:
    """Yield records; a footer is yielded ONLY after full manifest verification.

    Consumers may stage bytes while iterating, but must not publish anything
    before receiving the verified footer. Metadata memory stays constant.
    """
    validate_digest(version_id)
    digest = hashlib.sha256()
    count = total = 0
    config = footer = None
    try:
        with open_regular(path) as stream:
            while True:
                line = stream.readline(_MAX_RECORD + 1)
                if not line:
                    break
                if len(line) > _MAX_RECORD or not line.endswith(b"\n"):
                    raise IntegrityError("Manifest record is oversized or incomplete")
                digest.update(line)
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError) as exc:
                    raise IntegrityError("Malformed manifest JSON") from exc
                if not isinstance(record, dict):
                    raise IntegrityError("Manifest records must be objects")
                kind = record.get("type")
                if config is None:
                    if (
                        set(record) != {"type", "schema", "chunking"}
                        or kind != "header"
                        or record["schema"] != SCHEMA
                    ):
                        raise IntegrityError("Unsupported version manifest header")
                    fields = record["chunking"]
                    if (
                        not isinstance(fields, dict)
                        or set(fields)
                        != {"algorithm", "min_size", "average_size", "max_size"}
                        or fields["algorithm"] != ALGORITHM
                    ):
                        raise IntegrityError("Unsupported chunking algorithm")
                    try:
                        config = ChunkingConfig(
                            **{
                                key: value
                                for key, value in fields.items()
                                if key != "algorithm"
                            }
                        )
                    except (TypeError, ValueError) as exc:
                        raise IntegrityError("Invalid chunk bounds") from exc
                    yield "header", config
                elif footer is not None:
                    raise IntegrityError("Manifest has records after its footer")
                elif kind == "chunk":
                    if set(record) != {"type", "sha256", "size"}:
                        raise IntegrityError("Invalid chunk reference")
                    ref = ChunkRef(
                        validate_digest(record["sha256"]),
                        _integer(record["size"], positive=True),
                    )
                    if ref.size > config.max_size:
                        raise IntegrityError("Chunk exceeds declared maximum")
                    count += 1
                    total += ref.size
                    yield "chunk", ref
                elif kind == "footer":
                    if set(record) != {"type", "sha256", "size", "chunk_count"}:
                        raise IntegrityError("Invalid manifest footer")
                    footer = VersionManifest(
                        version_id,
                        validate_digest(record["sha256"]),
                        _integer(record["size"]),
                        _integer(record["chunk_count"]),
                        config,
                    )
                else:
                    raise IntegrityError("Unknown manifest record")
    except OSError as exc:
        raise IntegrityError("Version manifest cannot be read") from exc
    if (
        footer is None
        or count != footer.chunk_count
        or total != footer.size
        or digest.hexdigest() != version_id
    ):
        raise IntegrityError("Manifest digest, count, or size verification failed")
    yield "footer", footer
