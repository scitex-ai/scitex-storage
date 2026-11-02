"""Real dry-run/apply CLI behavior through lazy root dispatch."""

import json

from click.testing import CliRunner

from scitex_storage._cli import main


def _invoke(root, *arguments):
    return CliRunner().invoke(
        main, ["versions", "--store", str(root), *map(str, arguments)]
    )


def test_add_preview_is_stat_only_and_creates_nothing(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"original")
    root = tmp_path / "store"
    # Act
    result = _invoke(root, "add", source, "--yes", "--dry-run")
    value = json.loads(result.output)
    # Assert
    assert (
        result.exit_code,
        value["status"],
        value["reused_bytes"],
        root.exists(),
        source.read_bytes(),
    ) == (0, "planned", None, False, b"original")


def test_add_pointer_and_checkout_require_apply_and_emit_plain_json(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"experiment" * 10_000)
    root = tmp_path / "store"
    # Act
    added = _invoke(root, "add", source, "--yes")
    version = json.loads(added.output)["manifest"]["version_id"]
    destination = tmp_path / "restored"
    preview = _invoke(root, "checkout", version, destination, "--yes", "--dry-run")
    before = destination.exists()
    pointer = tmp_path / "pointer.json"
    pointer_preview = _invoke(root, "pointer", version, pointer)
    pointer_before = pointer.exists()
    pointed = _invoke(root, "pointer", version, pointer, "--yes")
    checked = _invoke(root, "checkout", version, destination, "--yes")
    # Assert
    assert (
        added.exit_code,
        json.loads(preview.output)["status"],
        before,
        pointer_preview.exit_code,
        pointer_before,
        json.loads(pointed.output)["status"],
        json.loads(checked.output)["status"],
        destination.read_bytes() == source.read_bytes(),
    ) == (0, "planned", False, 0, False, "pointer_created", "checked_out", True)


def test_existing_checkout_is_a_typed_refusal_without_overwrite(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"data")
    root = tmp_path / "store"
    # Act
    added = _invoke(root, "add", source, "--yes")
    version = json.loads(added.output)["manifest"]["version_id"]
    destination = tmp_path / "occupied"
    destination.write_bytes(b"keep")
    refused = _invoke(root, "checkout", version, destination, "--yes")
    value = json.loads(refused.output)
    # Assert
    assert (
        refused.exit_code,
        value["status"],
        value["error_type"],
        destination.read_bytes(),
    ) == (1, "refused", "FileExistsError", b"keep")


def test_inspect_claims_manifest_verification_only(tmp_path):
    # Arrange
    source = tmp_path / "data"
    source.write_bytes(b"data")
    root = tmp_path / "store"
    # Act
    added = _invoke(root, "add", source, "--yes")
    version = json.loads(added.output)["manifest"]["version_id"]
    inspected = _invoke(root, "inspect", version)
    value = json.loads(inspected.output)
    # Assert
    assert (inspected.exit_code, value["status"], value["content_verified"]) == (
        0,
        "manifest_verified",
        False,
    )
