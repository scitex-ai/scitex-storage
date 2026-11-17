"""Native fclones and the actual CLI produce the optional location breakdown."""

import json
import shutil

import pytest
from click.testing import CliRunner

from scitex_storage._cli import main


@pytest.fixture
def root(tmp_path):
    tree = tmp_path / "root"
    for name in ["a/one", "a/two", "a/deep/three", "b/four"]:
        path = tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"same exact content")
    return tree


@pytest.mark.parametrize("by,counts", [("parent", [2, 1, 1]), ("top-level", [3, 1])])
def test_native_cli_json_preserves_groups_and_adds_locations(root, by, counts):
    # Arrange
    runner = CliRunner()
    binary = shutil.which("fclones")
    # Act
    result = runner.invoke(
        main, ["find-duplicates", str(root), "--group-by", by, "--json"]
    )
    payload = json.loads(result.output)
    locations = payload["location_groups"][0]
    # Assert
    assert (
        binary is not None,
        result.exit_code,
        payload["group_count"],
        len(payload["groups"][0]),
        payload["group_by"],
        locations["relationship"],
        [len(row["files"]) for row in locations["locations"]],
    ) == (True, 0, 1, 4, by, "across-locations", counts)


def test_native_cli_text_describes_locations(root):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(main, ["find-duplicates", str(root), "--group-by", "parent"])
    # Assert
    assert (
        result.exit_code,
        "Duplicate locations (parent):" in result.output,
        "Group 1: across-locations" in result.output,
    ) == (0, True, True)


def test_size_candidates_cannot_be_presented_as_content_locations(root):
    # Arrange
    runner = CliRunner()
    # Act
    result = runner.invoke(
        main,
        [
            "find-duplicates",
            str(root),
            "--sizes-only",
            "--group-by",
            "parent",
            "--json",
        ],
    )
    # Assert
    assert (
        result.exit_code,
        "--group-by requires content hashing" in result.output,
    ) == (2, True)
