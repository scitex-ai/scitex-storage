"""Run identifiers cannot redirect archive or manifest paths."""

import json
import os
from pathlib import Path

import pytest

from scitex_storage._transfer._reclaim import (
    ReclaimEntry,
    ReclaimPlan,
    apply_reclaim,
    load_manifest,
    plan_reclaim,
    restore_reclaim,
)


def refusal(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    return ""


@pytest.fixture
def roots(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    old = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    source = tmp_path / "source.bin"
    source.write_bytes(b"owned source")
    try:
        yield home, source, tmp_path / "archive"
    finally:
        if old is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old


@pytest.fixture(
    params=[
        "",
        ".",
        "..",
        "../escape",
        "nested/run",
        "nested\\run",
        "\x00",
        None,
        123,
        "ABSOLUTE",
    ]
)
def invalid_id(request, tmp_path):
    if request.param == "ABSOLUTE":
        return str(tmp_path / "escaped-run")
    return request.param


def test_plan_refuses_run_paths_without_touching_source(roots, invalid_id):
    # Arrange
    home, source, archive = roots
    # Act
    error = refusal(
        lambda: plan_reclaim([source], run_id=invalid_id, archive_root=str(archive))
    )
    # Assert
    assert (
        "run_id" in error,
        source.read_bytes(),
        list(home.iterdir()),
        archive.exists(),
    ) == (True, b"owned source", [], False)


def test_apply_refuses_legacy_run_paths_before_any_move(roots, invalid_id):
    # Arrange -- even caller-created plans must not move then fail to record.
    home, source, archive = roots
    plan = ReclaimPlan(
        invalid_id,
        str(archive),
        [ReclaimEntry(str(source), str(archive / "copy"), 12, 1)],
    )
    # Act
    error = refusal(lambda: apply_reclaim(plan))
    # Assert
    assert (
        "run_id" in error,
        source.is_file(),
        archive.exists(),
        list(home.iterdir()),
    ) == (True, True, False, [])


def test_load_refuses_run_paths_before_manifest_access(roots, invalid_id):
    # Arrange
    home, source, archive = roots
    # Act
    error = refusal(lambda: load_manifest(invalid_id))
    # Assert
    assert ("run_id" in error, source.read_bytes(), list(home.iterdir())) == (
        True,
        b"owned source",
        [],
    )


@pytest.mark.parametrize("run_id", ["r1", "2026-1116-154500-123456", "整理.one_2"])
def test_regular_run_identifiers_still_round_trip(roots, run_id):
    # Arrange
    home, source, archive = roots
    plan = plan_reclaim([source], run_id=run_id, archive_root=str(archive))
    # Act
    manifest = apply_reclaim(plan)
    restored = restore_reclaim(manifest.run_id)
    # Assert
    assert (
        source.read_bytes(),
        restored.run_id,
        restored.restored,
        Path(plan.entries[0].archived).exists(),
    ) == (b"owned source", run_id, True, False)


def test_restore_refuses_a_manifest_recorded_for_another_run(roots):
    # Arrange -- real persisted manifest; no filesystem/model replacements.
    home, source, archive = roots
    plan = plan_reclaim([source], run_id="requested", archive_root=str(archive))
    apply_reclaim(plan)
    path = home / ".scitex/scitex-storage/runtime/reclaim-manifests/requested.json"
    payload = json.loads(path.read_text())
    payload["run_id"] = "another-run"
    path.write_text(json.dumps(payload))
    # Act
    error = refusal(lambda: restore_reclaim("requested"))
    # Assert
    assert (
        "run_id" in error,
        source.exists(),
        Path(plan.entries[0].archived).read_bytes(),
        (path.parent / "another-run.json").exists(),
    ) == (True, False, b"owned source", False)


@pytest.mark.parametrize("run_id", ["..", "../escape", "nested/run", "nested\\run", ""])
def test_cli_reports_invalid_run_id_as_a_clean_error(roots, run_id):
    # Arrange
    from click.testing import CliRunner
    from scitex_storage._cli import main

    home, source, archive = roots
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["reclaim-restore", run_id])
    # Assert
    assert (
        result.exit_code,
        "Error: run_id" in result.output,
        list(home.iterdir()),
        source.read_bytes(),
    ) == (1, True, [], b"owned source")
