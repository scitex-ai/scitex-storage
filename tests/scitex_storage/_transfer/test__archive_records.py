"""Tests for the archive records: unit kinds and the remote digest baseline."""

from scitex_storage._transfer._archive_records import (
    SOURCE_KIND_FILE,
    ArchiveManifest,
    remote_tree_digest,
)
from scitex_storage._transfer._content_verify import ContentManifest, tree_digest


def _manifest(**kw):
    base = dict(
        source="/volume/backups/db.dump",
        destination="scitex-nas-02",
        remote_path="/remote/volume/backups/db.dump",
        size_bytes=4,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
    )
    base.update(kw)
    return ArchiveManifest(**base)


def test_file_manifest_expects_a_one_entry_remote_tree():
    # Arrange
    manifest = _manifest(content_digest="a" * 64, source_kind=SOURCE_KIND_FILE)
    # Act
    expected = remote_tree_digest(manifest)
    # Assert
    assert expected == tree_digest(ContentManifest(digests={"db.dump": "a" * 64}))


def test_directory_manifest_expects_its_own_digest():
    # Arrange
    manifest = _manifest(content_digest="b" * 64)
    # Act
    expected = remote_tree_digest(manifest)
    # Assert
    assert expected == "b" * 64


def test_manifest_without_a_baseline_expects_nothing():
    # Arrange
    manifest = _manifest(source_kind=SOURCE_KIND_FILE)
    # Act
    expected = remote_tree_digest(manifest)
    # Assert
    assert expected == ""


def test_older_manifest_without_source_kind_loads_as_a_directory():
    # Arrange
    data = _manifest().to_dict()
    data.pop("source_kind")
    # Act
    manifest = ArchiveManifest.from_dict(data)
    # Assert
    assert manifest.source_kind == "directory"
