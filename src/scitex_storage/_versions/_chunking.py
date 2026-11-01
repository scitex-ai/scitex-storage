"""Bounded streaming Gear CDC; deliberately not a full FastCDC implementation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO, Iterator

ALGORITHM = "gear-cdc-v1"
_MASK64 = (1 << 64) - 1
# Fixed, reproducible table, independent of Python's randomized hash seed.
_GEAR = tuple(
    int.from_bytes(
        hashlib.sha256(f"scitex-storage/{ALGORITHM}/{i}".encode()).digest()[:8],
        "little",
    )
    for i in range(256)
)


@dataclass(frozen=True)
class ChunkingConfig:
    """Chunk bounds in bytes; average_size is the rolling-hash mask scale."""

    min_size: int = 64 * 1_024
    average_size: int = 256 * 1_024
    max_size: int = 1_024 * 1_024
    read_size: int = 64 * 1_024

    def __post_init__(self):
        values = (self.min_size, self.average_size, self.max_size, self.read_size)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Chunk and read sizes must be positive integers")
        if not self.min_size <= self.average_size <= self.max_size:
            raise ValueError("Require min_size <= average_size <= max_size")
        if self.average_size & (self.average_size - 1):
            raise ValueError("average_size must be a power of two")

    def manifest_fields(self) -> dict:
        # Buffer size does not affect boundaries or content/version identity.
        return {
            "algorithm": ALGORITHM,
            "min_size": self.min_size,
            "average_size": self.average_size,
            "max_size": self.max_size,
        }


def iter_chunks(stream: BinaryIO, config: ChunkingConfig) -> Iterator[bytes]:
    """Read at most read_size and buffer at most max_size bytes per chunk.

    File size is unrestricted. The final remainder may be below min_size.
    Boundaries depend on content rather than read-block/file offsets.
    """
    chunk = bytearray()
    fingerprint = 0
    mask = config.average_size - 1
    while True:
        block = stream.read(config.read_size)
        if not block:
            break
        for byte in block:
            chunk.append(byte)
            fingerprint = ((fingerprint << 1) + _GEAR[byte]) & _MASK64
            length = len(chunk)
            if length >= config.max_size or (
                length >= config.min_size and not fingerprint & mask
            ):
                yield bytes(chunk)
                chunk.clear()
                fingerprint = 0
    if chunk:
        yield bytes(chunk)
