"""Unit tests for the ``scitex-storage retire`` CLI command.

``--yes`` always contacts the archive host, so its live path is covered in
``_transfer/test__retire.py`` through the runner seam. These cover the
offline dry-run: validation, the plan, and the refusal exit code.
"""

import json

from click.testing import CliRunner

from scitex_storage._cli import main


def _manifest(tmp_path, **overrides):
    source = tmp_path / "data"
    source.mkdir()
    (source / "a.bin").write_bytes(b"alpha")
    fields = dict(
        source=str(source),
        destination="scitex-nas-02",
        remote_path="/share/archive/data",
        size_bytes=5,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
        verified="verified",
        verification_method="content",
        content_digest="f" * 64,
        retained_source=True,
    )
    fields.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(fields))
    return path


def test_cli_retire_is_a_registered_verb():
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["retire", "--help"])
    # Assert
    assert result.exit_code == 0


def test_cli_retire_dry_run_deletes_nothing(tmp_path):
    # Arrange
    path = _manifest(tmp_path)
    # Act
    CliRunner().invoke(main, ["retire", str(path)])
    # Assert
    assert (tmp_path / "data" / "a.bin").exists()


def test_cli_retire_dry_run_plans_ready(tmp_path):
    # Arrange
    path = _manifest(tmp_path)
    # Act
    result = CliRunner().invoke(main, ["retire", str(path), "--json"])
    # Assert
    assert json.loads(result.output)["ready"] is True


def test_cli_retire_refuses_unverified_manifest_with_exit_18(tmp_path):
    # Arrange
    path = _manifest(tmp_path, verified="mismatch")
    # Act
    result = CliRunner().invoke(main, ["retire", str(path)])
    # Assert
    assert result.exit_code == 18


def test_cli_retire_missing_manifest_fails(tmp_path):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["retire", str(tmp_path / "none.json")])
    # Assert
    assert result.exit_code != 0
