"""Real-file location breakdowns preserve content and filesystem identities."""

import itertools
import os
from pathlib import Path

import pytest

from scitex_storage import group_duplicates


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    paths = [
        root / name
        for name in [
            "a/one.txt",
            "a/two.txt",
            "a/deep/three.txt",
            "b/four.txt",
            "five.txt",
        ]
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"equal content")
    return root, paths


def _view(rows, root):
    return [
        (
            row["group_index"],
            row["relationship"],
            {
                str(Path(location["directory"]).relative_to(root)): [
                    str(Path(path).relative_to(root)) for path in location["files"]
                ]
                for location in row["locations"]
            },
        )
        for row in rows
    ]


def test_parent_grouping_preserves_all_locations(tree):
    # Arrange
    root, paths = tree
    # Act
    rows = group_duplicates([paths])
    # Assert
    assert _view(rows, root) == [
        (
            0,
            "across-locations",
            {
                ".": ["five.txt"],
                "a": ["a/one.txt", "a/two.txt"],
                "a/deep": ["a/deep/three.txt"],
                "b": ["b/four.txt"],
            },
        )
    ]


def test_top_level_groups_nested_parents_under_one_child(tree):
    # Arrange
    root, paths = tree
    # Act
    rows = group_duplicates([paths], by="top-level", roots=[root])
    # Assert
    assert _view(rows, root) == [
        (
            0,
            "across-locations",
            {
                ".": ["five.txt"],
                "a": ["a/deep/three.txt", "a/one.txt", "a/two.txt"],
                "b": ["b/four.txt"],
            },
        )
    ]


@pytest.mark.parametrize("by", ["parent", "top-level"])
def test_within_location_preserves_input_group_index(tree, by):
    # Arrange
    root, paths = tree
    # Act
    rows = group_duplicates([[], paths[:2]], by=by, roots=[root])
    # Assert
    assert _view(rows, root) == [
        (1, "within-location", {"a": ["a/one.txt", "a/two.txt"]})
    ]


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_nested_root_ownership_is_independent_of_argument_order(tree, order):
    # Arrange
    root, paths = tree
    roots = [root, root / "a", root / "a/deep"]
    # Act
    rows = group_duplicates([paths], by="top-level", roots=[roots[i] for i in order])
    # Assert
    assert _view(rows, root) == [
        (
            0,
            "across-locations",
            {
                ".": ["five.txt"],
                "a": ["a/one.txt", "a/two.txt"],
                "a/deep": ["a/deep/three.txt"],
                "b": ["b/four.txt"],
            },
        )
    ]


def test_directory_aliases_use_canonical_locations(tree, tmp_path):
    # Arrange
    root, paths = tree
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    # Act
    rows = group_duplicates(
        [[alias / path.relative_to(root) for path in paths]],
        by="top-level",
        roots=[alias, root],
    )
    # Assert
    assert rows == group_duplicates([paths], by="top-level", roots=[root])


@pytest.fixture(params=["hardlink", "symlink", "repeated"])
def aliases(tmp_path, request):
    original = tmp_path / "original"
    original.write_bytes(b"content")
    alias = tmp_path / "alias"
    if request.param == "hardlink":
        os.link(original, alias)
    elif request.param == "symlink":
        alias.symlink_to(original)
    else:
        alias = original
    return [original, alias]


def test_aliases_do_not_become_independent_duplicate_copies(aliases):
    # Arrange
    group = aliases
    # Act
    rows = group_duplicates([group])
    # Assert
    assert rows == []


def test_missing_member_is_an_error(tree):
    # Arrange
    root, paths = tree
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        group_duplicates([paths + [root / "missing"]])


def test_outside_root_member_is_an_error(tree, tmp_path):
    # Arrange
    root, paths = tree
    outside = tmp_path / "outside"
    outside.write_bytes(b"equal content")
    # Act
    # Assert
    with pytest.raises(ValueError, match="outside selected roots"):
        group_duplicates([[paths[0], outside]], by="top-level", roots=[root])


@pytest.mark.parametrize("kwargs", [{"by": "unknown"}, {"by": "top-level"}])
def test_invalid_grouping_configuration_is_an_error(kwargs):
    # Arrange
    groups = []
    # Act
    # Assert
    with pytest.raises(ValueError):
        group_duplicates(groups, **kwargs)
