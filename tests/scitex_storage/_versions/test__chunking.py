"""Bounded native FastCDC boundaries and insertion recovery on actual byte streams."""

import io
import random

import pytest

from scitex_storage._versions._chunking import ChunkingConfig, iter_chunks


def _refusal(action):
    try:
        action()
    except Exception as exc:
        return type(exc).__name__
    return None


def test_chunks_have_bounded_reads_sizes_and_exact_content():
    # Arrange
    data = random.Random(42).randbytes(300_000)

    class Reader(io.BytesIO):
        largest = 0

        def read(self, size=-1):
            if not 0 < size <= 997:
                raise AssertionError("Unbounded read")
            self.largest = max(self.largest, size)
            return super().read(size)

    stream = Reader(data)
    # Act
    chunks = list(iter_chunks(stream, ChunkingConfig(512, 2_048, 8_192, 997)))
    # Assert
    assert (
        b"".join(chunks) == data,
        max(map(len, chunks)) <= 8_192,
        min(map(len, chunks[:-1])) >= 512,
        stream.largest,
    ) == (True, True, True, 997)


def test_boundaries_do_not_depend_on_io_block_size():
    # Arrange
    data = random.Random(8).randbytes(100_000)
    # Act
    small = list(iter_chunks(io.BytesIO(data), ChunkingConfig(512, 2_048, 8_192, 37)))
    large = list(
        iter_chunks(io.BytesIO(data), ChunkingConfig(512, 2_048, 8_192, 65_536))
    )
    # Assert
    assert small == large


@pytest.mark.parametrize(
    "values", [(0, 1_024, 4_096), (2_000, 1_024, 4_096), (512, 1_500, 4_096)]
)
def test_invalid_chunking_bounds_are_refused(values):
    # Arrange
    # Act
    failure_1 = _refusal(lambda: ChunkingConfig(*values))
    # Assert
    assert (failure_1,) == ("ValueError",)


@pytest.mark.parametrize(
    "size", [0, 1, 63, 64, 255, 256, 1_023, 1_024, 8_191, 8_192, 8_193, 100_000]
)
def test_streaming_boundaries_equal_published_native_engine_at_eof(size):
    # Arrange
    from fastcdc.fastcdc_cy import fastcdc_cy

    data = random.Random(size).randbytes(size)
    config = ChunkingConfig(64, 256, 1_024, 37)
    # Act
    actual = [len(chunk) for chunk in iter_chunks(io.BytesIO(data), config)]
    expected = [
        chunk.length
        for chunk in fastcdc_cy(data, min_size=64, avg_size=256, max_size=1_024)
    ]
    # Assert
    assert actual == expected


def test_large_window_boundaries_equal_the_native_reference():
    # Arrange
    from fastcdc.fastcdc_cy import fastcdc_cy

    data = random.Random(92).randbytes(20_000_000)
    config = ChunkingConfig(read_size=997)
    # Act
    actual = [len(chunk) for chunk in iter_chunks(io.BytesIO(data), config)]
    expected = [
        chunk.length
        for chunk in fastcdc_cy(
            data,
            min_size=config.min_size,
            avg_size=config.average_size,
            max_size=config.max_size,
        )
    ]
    # Assert
    assert actual == expected


def test_missing_native_dependency_refuses_ingest_before_any_store_write(tmp_path):
    # Arrange
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[3] / "src"
    program = """
import json,sys
from pathlib import Path
from scitex_storage import VersionStore, NativeChunkerUnavailable
root=Path(sys.argv[1]);source=root/'input';source.write_bytes(b'data')
store=VersionStore(root/'objects')
try:
    store.ingest(source)
except NativeChunkerUnavailable as exc:
    sys.stdout.write(json.dumps({'error':type(exc).__name__,'store_exists':store.root.exists()}))
else:
    raise RuntimeError('unexpected native provider')
"""
    # Act
    result = subprocess.run(
        [sys.executable, "-S", "-c", program, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(source_root)},
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    # Assert
    assert (result.returncode, value) == (
        0,
        {"error": "NativeChunkerUnavailable", "store_exists": False},
    )


def test_package_without_native_extension_is_refused_without_slow_fallback(tmp_path):
    # Arrange
    import json
    import os
    import shutil
    import subprocess
    import sys
    from importlib.machinery import EXTENSION_SUFFIXES
    from importlib.metadata import distribution
    from pathlib import Path

    import fastcdc

    isolated = tmp_path / "provider"
    isolated.mkdir()
    copied = isolated / "fastcdc"
    shutil.copytree(Path(fastcdc.__file__).parent, copied)
    for path in copied.glob("fastcdc_cy*"):
        if any(path.name.endswith(suffix) for suffix in EXTENSION_SUFFIXES):
            path.unlink()
    dist = distribution("fastcdc")
    metadata = next(
        dist.locate_file(path).parent for path in dist.files if path.name == "METADATA"
    )
    shutil.copytree(metadata, isolated / metadata.name)
    source_root = Path(__file__).resolve().parents[3] / "src"
    program = """
import json,sys
from pathlib import Path
from scitex_storage import VersionStore, NativeChunkerUnavailable
root=Path(sys.argv[1]);source=root/'input';source.write_bytes(b'data')
store=VersionStore(root/'objects')
try:
    store.ingest(source)
except NativeChunkerUnavailable as exc:
    sys.stdout.write(json.dumps({
        'error':type(exc).__name__, 'store_exists':store.root.exists(),
        'imported':'fastcdc' in sys.modules}))
else:
    raise RuntimeError('unexpected native provider')
"""
    # Act
    result = subprocess.run(
        [sys.executable, "-S", "-c", program, str(tmp_path)],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(source_root), str(isolated)]),
        },
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    # Assert
    assert (result.returncode, value) == (
        0,
        {"error": "NativeChunkerUnavailable", "store_exists": False, "imported": False},
    )
