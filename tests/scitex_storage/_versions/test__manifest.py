"""Manifest digests, strict paths, and bounded-memory metadata traversal."""

import hashlib
import tracemalloc

import pytest

from scitex_storage._versions import ChunkingConfig, VersionStore
from scitex_storage._versions._manifest import encode_record, header


def _refusal(action):
    try:
        action()
    except Exception as exc:
        return type(exc).__name__
    return None


def test_manifest_identity_ignores_io_buffer_size(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(bytes(range(256)) * 1_000)
    # Act
    first = VersionStore(
        tmp_path / "objects", chunking=ChunkingConfig(512, 2_048, 8_192, 37)
    ).ingest(source)
    second = VersionStore(
        tmp_path / "objects", chunking=ChunkingConfig(512, 2_048, 8_192, 65_536)
    ).ingest(source)
    # Assert
    assert (first.manifest.version_id, second.new_chunks) == (
        second.manifest.version_id,
        0,
    )


def test_manifest_tamper_is_refused(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"measurement")
    store = VersionStore(tmp_path / "objects")
    # Act
    version = store.ingest(source).manifest.version_id
    manifest = store.root / "versions" / "sha256" / version[:2] / version
    manifest.chmod(0o600)
    manifest.write_bytes(manifest.read_bytes().replace(b"measurement", b"bad") + b" ")
    failure_1 = _refusal(lambda: store.read_manifest(version))
    # Assert
    assert (failure_1,) == ("IntegrityError",)


def test_large_reference_manifest_is_streamed_without_a_chunk_list(tmp_path):
    # Arrange
    records = 20_000
    root = tmp_path / "objects"
    staging = tmp_path / "manifest"
    digest = hashlib.sha256()
    with staging.open("wb") as output:
        for record in [header(ChunkingConfig())]:
            encoded = encode_record(record)
            output.write(encoded)
            digest.update(encoded)
        for _ in range(records):
            encoded = encode_record(
                {"type": "chunk", "sha256": "a" * 64, "size": 65_536}
            )
            output.write(encoded)
            digest.update(encoded)
        encoded = encode_record(
            {
                "type": "footer",
                "sha256": "b" * 64,
                "size": records * 65_536,
                "chunk_count": records,
            }
        )
        output.write(encoded)
        digest.update(encoded)
    version = digest.hexdigest()
    path = root / "versions" / "sha256" / version[:2] / version
    path.parent.mkdir(parents=True)
    staging.rename(path)
    tracemalloc.start()
    # Act
    manifest = VersionStore(root).read_manifest(version)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Assert
    assert (manifest.chunk_count, peak < 1_000_000) == (records, True)


@pytest.mark.parametrize("version", ["../outside", "a" * 63, "A" * 64, "z" * 64])
def test_invalid_version_ids_cannot_become_paths(tmp_path, version):
    # Arrange
    # Act
    failure_1 = _refusal(
        lambda: VersionStore(tmp_path / "missing").read_manifest(version)
    )
    # Assert
    assert (failure_1, (not (tmp_path / "missing").exists())) == (
        "IntegrityError",
        True,
    )


def test_internal_symlink_directory_is_refused(tmp_path):
    # Arrange
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "objects"
    root.mkdir()
    (root / "versions").symlink_to(outside, target_is_directory=True)
    # Act
    failure_1 = _refusal(lambda: VersionStore(root).read_manifest("a" * 64))
    # Assert
    assert (failure_1, (list(outside.iterdir()) == [])) == ("IntegrityError", True)
