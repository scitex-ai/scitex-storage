"""Unit tests for scitex_storage._report (text + JSON rendering)."""

from scitex_storage._report import (
    format_count,
    format_report,
    format_root_report,
    format_size,
    to_json_dict,
)
from scitex_storage._scan import scan


def _touch(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_format_size_bytes():
    # Arrange
    n = 500
    # Act
    rendered = format_size(n)
    # Assert
    assert rendered == "500 B"


def test_format_size_megabytes():
    # Arrange
    n = 5 * 1024 * 1024
    # Act
    rendered = format_size(n)
    # Assert
    assert rendered == "5.0 MB"


def test_format_size_gigabytes():
    # Arrange
    n = 3 * 1024**3
    # Act
    rendered = format_size(n)
    # Assert
    assert rendered == "3.0 GB"


def test_format_count_uses_thousands_separator():
    # Arrange
    n = 1234
    # Act
    rendered = format_count(n)
    # Assert
    assert rendered == "1,234"


def test_format_root_report_includes_root_path(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert str(tmp_path.resolve()) in text


def test_format_root_report_lists_child_name(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert "child" in text


def test_format_root_report_has_files_column_header(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert "FILES" in text


def test_format_root_report_handles_empty_tree(tmp_path):
    # Arrange
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert "(empty)" in text


def test_format_report_joins_multiple_roots(tmp_path):
    # Arrange
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    _touch(root_a / "a.bin", 10)
    _touch(root_b / "b.bin", 20)
    results = [scan(root_a), scan(root_b)]
    # Act
    text = format_report(results)
    # Assert
    assert str(root_a.resolve()) in text and str(root_b.resolve()) in text


def test_to_json_dict_has_roots_key(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    results = [scan(tmp_path)]
    # Act
    payload = to_json_dict(results)
    # Assert
    assert "roots" in payload


def test_to_json_dict_root_has_expected_keys(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    results = [scan(tmp_path)]
    # Act
    payload = to_json_dict(results)
    # Assert
    assert {"root", "total_size_bytes", "total_files", "children"} <= payload[
        "roots"
    ][0].keys()


def test_to_json_dict_respects_top_limit(tmp_path):
    # Arrange
    for i in range(5):
        _touch(tmp_path / f"child{i}" / "a.bin", 10)
    results = [scan(tmp_path)]
    # Act
    payload = to_json_dict(results, top=2)
    # Assert
    assert len(payload["roots"][0]["children"]) == 2


# EOF
