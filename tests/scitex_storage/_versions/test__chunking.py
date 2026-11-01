"""Bounded Gear CDC boundaries and insertion recovery on actual byte streams."""

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
