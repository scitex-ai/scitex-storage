"""Real filesystem durability boundaries; no mocked I/O."""

from scitex_storage._transfer._durable import atomic_json, rename_noreplace


def test_atomic_json_serialization_failure_preserves_existing_record(tmp_path):
    # Arrange
    path = tmp_path / "record.json"
    atomic_json(path, {"previous": "intact"})
    before = path.read_bytes()
    # Act
    failed = False
    try:
        atomic_json(path, {"bad": object()})
    except TypeError:
        failed = True
    # Assert
    assert (failed, path.read_bytes(), list(tmp_path.iterdir())) == (
        True,
        before,
        [path],
    )


def test_kernel_no_replace_rename_preserves_existing_destination(tmp_path):
    # Arrange
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"original")
    destination.write_bytes(b"new occupant")
    # Act
    refused = False
    try:
        rename_noreplace(source, destination)
    except FileExistsError:
        refused = True
    # Assert
    assert (refused, source.read_bytes(), destination.read_bytes()) == (
        True,
        b"original",
        b"new occupant",
    )
