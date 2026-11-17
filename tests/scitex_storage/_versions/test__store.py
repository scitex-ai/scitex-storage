"""Real-file additive snapshots, deduplication, and verified no-clobber checkout."""

import hashlib
import random

import pytest

from scitex_storage._versions import (
    ChunkingConfig,
    VersionStore,
)


def _refusal(action):
    try:
        action()
    except Exception as exc:
        return type(exc).__name__
    return None


@pytest.fixture
def data_store(tmp_path):
    source = tmp_path / "experiment.bin"
    source.write_bytes(random.Random(928).randbytes(700_000))
    return source, VersionStore(
        tmp_path / "objects", chunking=ChunkingConfig(1_024, 4_096, 16_384, 4_096)
    )


def test_constructor_and_manifest_read_do_not_create_store(tmp_path):
    # Arrange
    root = tmp_path / "absent"
    store = VersionStore(root)
    # Act
    failure_1 = _refusal(lambda: store.read_manifest("a" * 64))
    # Assert
    assert (failure_1, (not root.exists())) == ("IntegrityError", True)


def test_ingest_checkout_and_repeat_reuse_preserve_source(data_store, tmp_path):
    # Arrange
    source, store = data_store
    before = source.stat()
    # Act
    first = store.ingest(source)
    second = store.ingest(source)
    result = store.checkout(first.manifest.version_id, tmp_path / "restored.bin")
    after = source.stat()
    # Assert
    assert (
        result.read_bytes() == source.read_bytes(),
        first.manifest.file_sha256,
        second.manifest.version_id == first.manifest.version_id,
        second.new_chunks,
        second.reused_bytes,
        before.st_size == after.st_size,
        before.st_mtime_ns == after.st_mtime_ns,
    ) == (
        True,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        True,
        0,
        before.st_size,
        True,
        True,
    )


def test_insertion_reuses_unchanged_chunks(data_store, tmp_path):
    # Arrange
    source, store = data_store
    baseline = source.read_bytes()
    # Act
    first = store.ingest(source)
    source.write_bytes(
        baseline[:120_000] + b"new instrument record\n" * 13 + baseline[120_000:]
    )
    second = store.ingest(source)
    checked = store.checkout(second.manifest.version_id, tmp_path / "v2.bin")
    # Assert
    assert (
        second.reused_bytes / len(baseline) > 0.90,
        second.manifest.version_id != first.manifest.version_id,
        checked.read_bytes() == source.read_bytes(),
    ) == (True, True, True)


def test_empty_file_is_a_verified_zero_chunk_version(tmp_path):
    # Arrange
    source = tmp_path / "empty"
    source.touch()
    store = VersionStore(tmp_path / "objects")
    # Act
    result = store.ingest(source)
    target = store.checkout(result.manifest.version_id, tmp_path / "restored")
    # Assert
    assert (result.manifest.size, result.manifest.chunk_count, target.read_bytes()) == (
        0,
        0,
        b"",
    )


def test_existing_checkout_and_broken_symlink_are_never_overwritten(
    data_store, tmp_path
):
    # Arrange
    source, store = data_store
    # Act
    version = store.ingest(source).manifest.version_id
    target = tmp_path / "occupied"
    target.write_bytes(b"keep me")
    failure_1 = _refusal(lambda: store.checkout(version, target))
    link = tmp_path / "broken"
    link.symlink_to(tmp_path / "missing")
    failure_2 = _refusal(lambda: store.checkout(version, link))
    # Assert
    assert (
        failure_1,
        failure_2,
        ((target.read_bytes(), link.is_symlink()) == (b"keep me", True)),
    ) == ("FileExistsError", "FileExistsError", True)


def test_racing_checkout_occupant_is_preserved(data_store, tmp_path):
    # Arrange
    source, store = data_store
    # Act
    version = store.ingest(source).manifest.version_id
    target = tmp_path / "race"

    def racing_occupant(done, total):
        if not target.exists():
            target.write_bytes(b"new occupant")

    failure = _refusal(
        lambda: store.checkout(version, target, on_progress=racing_occupant)
    )
    # Assert
    assert (failure, target.read_bytes(), list(tmp_path.glob(".stx-checkout-*"))) == (
        "FileExistsError",
        b"new occupant",
        [],
    )


def test_changed_source_does_not_publish_a_version(data_store):
    # Arrange
    source, store = data_store
    changed = False

    def concurrent_change(done, total):
        nonlocal changed
        if not changed:
            with source.open("ab") as output:
                output.write(b"concurrent change")
            changed = True

    # Act
    failure = _refusal(lambda: store.ingest(source, on_progress=concurrent_change))
    # Assert
    assert (failure, list((store.root / "versions").rglob("sha256/*/*"))) == (
        "SourceChangedError",
        [],
    )


def test_corrupt_chunk_refuses_checkout_without_partial_destination(
    data_store, tmp_path
):
    # Arrange
    source, store = data_store
    # Act
    version = store.ingest(source).manifest.version_id
    ref = next(store.iter_chunks(version))
    chunk = store.root / "chunks" / "sha256" / ref.sha256[:2] / ref.sha256
    chunk.chmod(0o600)
    chunk.write_bytes(b"corruption")
    target = tmp_path / "restored"
    failure_1 = _refusal(lambda: store.checkout(version, target))
    # Assert
    assert (
        failure_1,
        ((target.exists(), list(tmp_path.glob(".stx-checkout-*"))) == (False, [])),
    ) == ("IntegrityError", True)


def test_corrupt_existing_object_is_not_repaired_by_overwrite(data_store):
    # Arrange
    source, store = data_store
    # Act
    version = store.ingest(source).manifest.version_id
    ref = next(store.iter_chunks(version))
    chunk = store.root / "chunks" / "sha256" / ref.sha256[:2] / ref.sha256
    chunk.chmod(0o600)
    chunk.write_bytes(b"corruption")
    failure_1 = _refusal(lambda: store.ingest(source))
    # Assert
    assert (failure_1, (chunk.read_bytes() == b"corruption")) == (
        "IntegrityError",
        True,
    )


def test_source_symlink_and_source_inside_store_are_refused(data_store, tmp_path):
    # Arrange
    source, store = data_store
    link = tmp_path / "input-link"
    link.symlink_to(source)
    # Act
    failure_1 = _refusal(lambda: store.ingest(link))
    version = store.ingest(source).manifest.version_id
    ref = next(store.iter_chunks(version))
    object_file = store.root / "chunks" / "sha256" / ref.sha256[:2] / ref.sha256
    failure_2 = _refusal(lambda: store.ingest(object_file))
    # Assert
    assert (failure_1, failure_2, (source.exists())) == (
        "ValueError",
        "ValueError",
        True,
    )


def test_documented_temporary_example_runs_and_cleans_its_files():
    # Arrange
    from pathlib import Path

    document = Path(__file__).resolve().parents[3] / "docs/content-versions.md"
    example = document.read_text().split("```python\n", 1)[1].split("```", 1)[0]
    namespace = {}
    # Act
    exec(compile(example, str(document), "exec"), namespace)
    # Assert
    assert not namespace["root"].exists()


def test_progress_callback_can_cancel_without_publishing_or_changing_source(data_store):
    # Arrange
    source, store = data_store
    before = source.read_bytes()

    def cancel(done, total):
        raise RuntimeError("cancelled by caller")

    # Act
    failure = _refusal(lambda: store.ingest(source, on_progress=cancel))
    manifests = list((store.root / "versions").rglob("sha256/*/*"))
    # Assert
    assert (failure, manifests, source.read_bytes() == before) == (
        "RuntimeError",
        [],
        True,
    )
