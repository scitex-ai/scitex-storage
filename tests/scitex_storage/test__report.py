"""Unit tests for scitex_storage._report (text + JSON rendering)."""

from pathlib import Path

import pytest

from scitex_storage._report import (
    duplicates_to_json_dict,
    format_count,
    format_duplicates_report,
    format_report,
    format_root_report,
    format_size,
    to_json_dict,
)
from scitex_storage._measure._scan import scan


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


@pytest.mark.requires_fd
def test_format_root_report_includes_root_path(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert str(tmp_path.resolve()) in text


@pytest.mark.requires_fd
def test_format_root_report_lists_child_name(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    result = scan(tmp_path)
    # Act
    text = format_root_report(result)
    # Assert
    assert "child" in text


@pytest.mark.requires_fd
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


@pytest.mark.requires_fd
def test_to_json_dict_has_roots_key(tmp_path):
    # Arrange
    _touch(tmp_path / "child" / "a.bin", 10)
    results = [scan(tmp_path)]
    # Act
    payload = to_json_dict(results)
    # Assert
    assert "roots" in payload


@pytest.mark.requires_fd
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


@pytest.mark.requires_fd
def test_to_json_dict_respects_top_limit(tmp_path):
    # Arrange
    for i in range(5):
        _touch(tmp_path / f"child{i}" / "a.bin", 10)
    results = [scan(tmp_path)]
    # Act
    payload = to_json_dict(results, top=2)
    # Assert
    assert len(payload["roots"][0]["children"]) == 2


def test_format_duplicates_report_handles_no_groups():
    # Arrange
    groups = []
    # Act
    text = format_duplicates_report(groups)
    # Assert
    assert "No duplicate files found." == text


def test_format_duplicates_report_lists_group_paths(tmp_path):
    # Arrange
    a = _touch(tmp_path / "a.bin", 10)
    b = _touch(tmp_path / "b.bin", 10)
    groups = [[a, b]]
    # Act
    text = format_duplicates_report(groups)
    # Assert
    assert str(a) in text and str(b) in text


def test_duplicates_to_json_dict_has_group_count():
    # Arrange
    groups = [[Path("/a"), Path("/b")]]
    # Act
    payload = duplicates_to_json_dict(groups)
    # Assert
    assert payload["group_count"] == 1


def test_duplicates_to_json_dict_renders_paths_as_strings():
    # Arrange
    groups = [[Path("/a"), Path("/b")]]
    # Act
    payload = duplicates_to_json_dict(groups)
    # Assert
    assert payload["groups"] == [["/a", "/b"]]


def _archived_manifest(method):
    from scitex_storage._transfer._archive import ArchiveManifest

    return ArchiveManifest(
        source="/data/old",
        destination="nas2",
        remote_path="~/scitex-storage-archive/data/old",
        size_bytes=10,
        file_count=1,
        checksummed=True,
        archived_at=0.0,
        verification_method=method,
    )


def _archive_plan(tmp_path):
    from scitex_storage._transfer._archive import ArchivePlan

    return ArchivePlan(
        source=tmp_path / "old",
        destination="nas2",
        remote_path="~/scitex-storage-archive/old",
        size_bytes=10,
        file_count=1,
        manifest_path=tmp_path / "m.json",
    )


def test_format_archive_report_names_the_gate_that_cleared_the_delete(tmp_path):
    # Arrange -- a TALLY-only verdict with rsync checksumming ON. The old line
    # printed "checksummed=True" alone, which reads as "content verified" when
    # the delete was in fact cleared by a count+size tally. One word standing
    # for two different guarantees is how an operator over-trusts a delete.
    from scitex_storage._report import format_archive_report

    manifest = _archived_manifest("tally")
    # Act
    text = format_archive_report(_archive_plan(tmp_path), applied=True, manifest=manifest)
    # Assert
    assert "delete cleared by: tally" in text


def test_format_archive_report_says_content_when_the_content_gate_ran(tmp_path):
    # Arrange -- same rsync flag, different gate. The two runs must not render
    # identically, or the report cannot tell them apart for the reader either.
    from scitex_storage._report import format_archive_report

    manifest = _archived_manifest("content")
    # Act
    text = format_archive_report(_archive_plan(tmp_path), applied=True, manifest=manifest)
    # Assert
    assert "delete cleared by: content" in text


# EOF
