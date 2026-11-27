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


def test_inspect_output_choice_preserves_default_json_and_read_only_state(tmp_path):
    # Arrange
    source = tmp_path / "experiment"
    source.write_bytes(b"owned metadata fixture")
    root = tmp_path / "store"
    added = _invoke(root, "add", source, "--yes")
    version = json.loads(added.stdout)["manifest"]["version_id"]
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    # Act
    default = _invoke(root, "inspect", version)
    explicit = _invoke(root, "inspect", version, "--json")
    human = _invoke(root, "inspect", version, "--human")
    after = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    observed = {
        "exits": [default.exit_code, explicit.exit_code, human.exit_code],
        "json_identical": json.loads(default.stdout) == json.loads(explicit.stdout),
        "content_verified": json.loads(explicit.stdout)["content_verified"],
        "human_version": f"Version: {version}" in human.stdout,
        "human_scope": "Manifest: verified\nContent: not verified" in human.stdout,
        "human_size": f"Size: {source.stat().st_size} bytes" in human.stdout,
        "unchanged": before == after
        and source.read_bytes() == b"owned metadata fixture",
    }
    # Assert
    assert observed == {
        "exits": [0, 0, 0],
        "json_identical": True,
        "content_verified": False,
        "human_version": True,
        "human_scope": True,
        "human_size": True,
        "unchanged": True,
    }


def test_create_pointer_and_legacy_name_share_confirmation_and_no_overwrite(tmp_path):
    # Arrange
    source = tmp_path / "experiment"
    source.write_bytes(b"owned pointer fixture")
    root = tmp_path / "store"
    added = _invoke(root, "add", source, "--yes")
    version = json.loads(added.stdout)["manifest"]["version_id"]
    planned_path, new_path, old_path = [
        tmp_path / name for name in ("planned", "new", "old")
    ]
    # Act
    preview = _invoke(
        root, "create-pointer", version, planned_path, "--yes", "--dry-run"
    )
    created = _invoke(root, "create-pointer", version, new_path, "--yes")
    legacy = _invoke(root, "pointer", version, old_path, "--yes")
    bytes_before = new_path.read_bytes()
    refused = _invoke(root, "create-pointer", version, new_path, "--yes")
    observed = {
        "exits": [
            preview.exit_code,
            created.exit_code,
            legacy.exit_code,
            refused.exit_code,
        ],
        "planned": json.loads(preview.stdout)["status"],
        "no_preview_file": not planned_path.exists(),
        "created_statuses": [json.loads(r.stdout)["status"] for r in (created, legacy)],
        "same_pointer": bytes_before == old_path.read_bytes(),
        "refusal": json.loads(refused.stdout)["error_type"],
        "preserved": new_path.read_bytes() == bytes_before
        and source.read_bytes() == b"owned pointer fixture",
    }
    # Assert
    assert observed == {
        "exits": [0, 0, 0, 1],
        "planned": "planned",
        "no_preview_file": True,
        "created_statuses": ["pointer_created", "pointer_created"],
        "same_pointer": True,
        "refusal": "FileExistsError",
        "preserved": True,
    }


def test_legacy_pointer_warns_only_on_stderr_with_private_runtime_state(tmp_path):
    # Arrange
    source = tmp_path / "experiment"
    source.write_bytes(b"owned deprecation fixture")
    root = tmp_path / "store"
    version = json.loads(_invoke(root, "add", source, "--yes").stdout)["manifest"][
        "version_id"
    ]
    runtime = tmp_path / "private-runtime"
    runtime.mkdir(mode=0o700)
    destination = tmp_path / "planned-pointer"
    argv = ["versions", "--store", str(root), "pointer", version, str(destination)]
    # Act
    first = CliRunner().invoke(main, argv, env={"XDG_RUNTIME_DIR": str(runtime)})
    second = CliRunner().invoke(main, argv, env={"XDG_RUNTIME_DIR": str(runtime)})
    observed = {
        "exits": [first.exit_code, second.exit_code],
        "stdout_equal": json.loads(first.stdout) == json.loads(second.stdout),
        "planned": json.loads(first.stdout)["status"],
        "warning": "deprecated" in first.stderr and "create-pointer" in first.stderr,
        "warn_once": second.stderr == "",
        "destination_absent": not destination.exists(),
    }
    # Assert
    assert observed == {
        "exits": [0, 0],
        "stdout_equal": True,
        "planned": "planned",
        "warning": True,
        "warn_once": True,
        "destination_absent": True,
    }
