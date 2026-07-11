"""Unit tests for scitex_storage._report (text + JSON rendering)."""

from scitex_storage._report import format_size, format_text_report, to_json_dict
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


def test_format_text_report_includes_root(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_text_report(result)
    # Assert
    assert str(tmp_path) in text


def test_format_text_report_lists_scanned_file(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_text_report(result)
    # Assert
    assert "a.bin" in text


def test_format_text_report_handles_empty_tree(tmp_path):
    # Arrange
    result = scan(tmp_path)
    # Act
    text = format_text_report(result)
    # Assert
    assert "no files found" in text


def test_to_json_dict_has_expected_keys(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    payload = to_json_dict(result)
    # Assert
    assert {"root", "files_scanned", "total_size_bytes", "top_candidates"} <= payload.keys()


def test_to_json_dict_top_candidates_matches_file_count(tmp_path):
    # Arrange
    _touch(tmp_path / "a.bin", 10)
    _touch(tmp_path / "b.bin", 20)
    result = scan(tmp_path)
    # Act
    payload = to_json_dict(result, top=1)
    # Assert
    assert len(payload["top_candidates"]) == 1
