"""Bounded streaming adapter around fastcdc-py's published native bytes API."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from importlib.machinery import EXTENSION_SUFFIXES, PathFinder
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from typing import BinaryIO, Callable, Iterator

ENGINE_VERSION = "1.7.0"
ALGORITHM = "fastcdc-py-1.7.0-cython-v1"


class NativeChunkerUnavailable(RuntimeError):
    """The declared native chunker is absent; no slow fallback is permitted."""


@lru_cache(maxsize=1)
def require_native() -> Callable:
    """Preflight the normal declared package without triggering its slow fallback."""
    try:
        installed = version("fastcdc")
    except PackageNotFoundError as exc:
        raise NativeChunkerUnavailable(
            "Install the declared fastcdc==1.7.0 dependency"
        ) from exc
    if installed != ENGINE_VERSION:
        raise NativeChunkerUnavailable(
            f"This format requires fastcdc=={ENGINE_VERSION}; found {installed}"
        )
    package = find_spec("fastcdc")
    native = (
        PathFinder.find_spec("fastcdc.fastcdc_cy", package.submodule_search_locations)
        if package
        else None
    )
    if native is None or not any(
        (native.origin or "").endswith(suffix) for suffix in EXTENSION_SUFFIXES
    ):
        raise NativeChunkerUnavailable(
            "fastcdc==1.7.0 has no compiled native backend. Use a supported wheel "
            "or its normal Cython source build; no pure-Python fallback is used."
        )
    try:
        return import_module("fastcdc.fastcdc_cy").fastcdc_cy
    except ImportError as exc:
        raise NativeChunkerUnavailable(
            "The installed fastcdc native backend cannot load"
        ) from exc


@dataclass(frozen=True)
class ChunkingConfig:
    """Chunk bounds in bytes; read_size changes I/O only, never boundaries."""

    min_size: int = 512 * 1_024
    average_size: int = 2 * 1_024 * 1_024
    max_size: int = 8 * 1_024 * 1_024
    read_size: int = 64 * 1_024

    def __post_init__(self):
        values = (self.min_size, self.average_size, self.max_size, self.read_size)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Chunk and read sizes must be positive integers")
        if not self.min_size <= self.average_size <= self.max_size:
            raise ValueError("Require min_size <= average_size <= max_size")
        if self.average_size & (self.average_size - 1):
            raise ValueError("average_size must be a power of two")
        if not (
            64 <= self.min_size <= 67_108_864
            and 256 <= self.average_size <= 268_435_456
            and 1_024 <= self.max_size <= 1_073_741_824
        ):
            raise ValueError("Chunk bounds exceed fastcdc==1.7.0's supported ranges")

    def manifest_fields(self) -> dict:
        return {
            "algorithm": ALGORITHM,
            "min_size": self.min_size,
            "average_size": self.average_size,
            "max_size": self.max_size,
        }


def iter_chunks(stream: BinaryIO, config: ChunkingConfig) -> Iterator[bytes]:
    """Read bounded blocks/windows, delegating every cut point to native FastCDC.

    Upstream paths/file objects mmap the entire file. Only bounded bytes are
    passed here. A full max_size window determines the next chunk exactly;
    shorter windows are passed only at EOF, preserving upstream boundaries.
    """
    native = require_native()
    pending = bytearray()
    exhausted = False
    while True:
        while len(pending) < config.max_size and not exhausted:
            block = stream.read(min(config.read_size, config.max_size - len(pending)))
            if not block:
                exhausted = True
                break
            pending.extend(block)
        if not pending:
            return
        window = bytes(pending)
        iterator = native(
            window,
            min_size=config.min_size,
            avg_size=config.average_size,
            max_size=config.max_size,
            fat=False,
        )
        try:
            cut = next(iterator).length
        finally:
            iterator.close()
        if not 0 < cut <= len(window):
            raise RuntimeError("Native chunker returned an invalid boundary")
        data = window[:cut]
        del iterator, window
        del pending[:cut]
        yield data
