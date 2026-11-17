"""Nested-root duplicate ownership uses real files and stable matrix ordering."""

import pytest


def _nested_overlap_files(tmp_path):
    roots = [
        tmp_path / "outer",
        tmp_path / "outer" / "child",
        tmp_path / "outer" / "child" / "deep",
    ]
    files = []
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "copy.bin"
        path.write_bytes(b"data")
        files.append(path)
    return roots, files


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_overlap_nested_roots_keep_cross_folder_bytes(tmp_path, order):
    # Arrange
    from scitex_storage._measure._duplicates import overlap_bytes

    roots, files = _nested_overlap_files(tmp_path)
    parent, child = map(str, roots[:2])
    selected = [roots[index] for index in order]
    # Act
    matrix = overlap_bytes([files[:2]], selected)
    # Assert
    assert {
        "matrix": matrix,
        "rows": list(matrix),
        "columns": [list(row) for row in matrix.values()],
    } == {
        "matrix": {parent: {parent: 0, child: 4}, child: {parent: 4, child: 0}},
        "rows": list(map(str, selected)),
        "columns": [list(map(str, selected))] * 2,
    }


@pytest.mark.parametrize(
    "order", [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
)
def test_overlap_uses_deepest_selected_root_in_every_order(tmp_path, order):
    # Arrange
    from scitex_storage._measure._duplicates import overlap_bytes

    roots, files = _nested_overlap_files(tmp_path)
    parent, child, deep = map(str, roots)
    # Act
    matrix = overlap_bytes([files], [roots[index] for index in order])
    # Assert
    assert matrix == {
        parent: {parent: 0, child: 4, deep: 4},
        child: {parent: 4, child: 0, deep: 4},
        deep: {parent: 4, child: 4, deep: 0},
    }


def test_overlap_canonicalizes_an_aliased_nested_root(tmp_path):
    # Arrange
    from scitex_storage._measure._duplicates import overlap_bytes

    roots, files = _nested_overlap_files(tmp_path)
    alias = tmp_path / "child-alias"
    alias.symlink_to(roots[1], target_is_directory=True)
    parent, child = map(str, roots[:2])
    # Act
    matrix = overlap_bytes([files[:2]], [roots[0], alias])
    # Assert
    assert matrix == {parent: {parent: 0, child: 4}, child: {parent: 4, child: 0}}
