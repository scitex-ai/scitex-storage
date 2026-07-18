"""Unit tests for the document_sorter config loader.

NO MOCKS (PA-306): the loader takes the config PATH as an argument, so every
case writes a real yaml to tmp_path and reads it back -- no env sandboxing,
no monkeypatch. ONE assertion per test (PA-307), AAA-structured.
"""

from pathlib import Path

import pytest

from scitex_storage._document_pipeline._config import (
    DocumentSorterConfig,
    load_config,
)

_FULL = """\
document_sorter:
  inbox: "{inbox}"
  root: "{root}"
  archive: "{archive}"
  categories: [finance, contracts, misc]
  keep_original: [passport, mynumber_card]
  ocr:
    engine: easyocr
    languages: [ja, en]
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def _full_config(tmp_path: Path) -> Path:
    text = _FULL.format(
        inbox=tmp_path / "inbox",
        root=tmp_path / "scans",
        archive=tmp_path / "scans" / "90_archive",
    )
    return _write(tmp_path / "config.yaml", text)


def test_load_config_reads_inbox(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.inbox == tmp_path / "inbox"


def test_load_config_reads_root(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.root == tmp_path / "scans"


def test_load_config_reads_archive(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.archive == tmp_path / "scans" / "90_archive"


def test_load_config_parses_categories(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.categories == ("finance", "contracts", "misc")


def test_load_config_parses_keep_original(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.keep_original == ("passport", "mynumber_card")


def test_load_config_parses_ocr_languages(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.ocr_languages == ("ja", "en")


def test_load_config_ocr_enabled_defaults_true(tmp_path):
    # Arrange -- the sorter WANTS OCR on image scans, so default is on.
    path = _write(
        tmp_path / "c.yaml",
        f'document_sorter:\n  inbox: "{tmp_path}/in"\n  root: "{tmp_path}/s"\n',
    )
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.ocr_enabled is True


def test_load_config_ocr_enabled_can_be_disabled(tmp_path):
    # Arrange
    path = _write(
        tmp_path / "c.yaml",
        f'document_sorter:\n  inbox: "{tmp_path}/in"\n  root: "{tmp_path}/s"\n'
        "  ocr:\n    enabled: false\n",
    )
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.ocr_enabled is False


def test_load_config_expands_tilde_in_root(tmp_path):
    # Arrange
    path = _write(
        tmp_path / "c.yaml",
        'document_sorter:\n  inbox: "~/in"\n  root: "~/scans"\n',
    )
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.root == Path("~/scans").expanduser()


def test_load_config_defaults_archive_under_root(tmp_path):
    # Arrange
    path = _write(
        tmp_path / "c.yaml",
        f'document_sorter:\n  inbox: "{tmp_path}/in"\n  root: "{tmp_path}/scans"\n',
    )
    # Act
    cfg = load_config(path)
    # Assert
    assert cfg.archive == tmp_path / "scans" / "90_archive"


def test_load_config_missing_file_raises(tmp_path):
    # Arrange
    path = tmp_path / "nope.yaml"
    # Act
    # Assert
    with pytest.raises(FileNotFoundError):
        load_config(path)


def test_load_config_missing_section_raises(tmp_path):
    # Arrange
    path = _write(tmp_path / "c.yaml", "something_else: 1\n")
    # Act
    # Assert
    with pytest.raises(ValueError):
        load_config(path)


def test_load_config_missing_inbox_key_raises(tmp_path):
    # Arrange
    path = _write(tmp_path / "c.yaml", f'document_sorter:\n  root: "{tmp_path}/s"\n')
    # Act
    # Assert
    with pytest.raises(ValueError):
        load_config(path)


def test_sorted_root_is_under_root(tmp_path):
    # Arrange
    cfg = load_config(_full_config(tmp_path))
    # Act
    sorted_root = cfg.sorted_root
    # Assert
    assert sorted_root == tmp_path / "scans" / "10_sorted"


def test_index_path_is_under_root(tmp_path):
    # Arrange
    cfg = load_config(_full_config(tmp_path))
    # Act
    index_path = cfg.index_path
    # Assert
    assert index_path == tmp_path / "scans" / "_index" / "index.jsonl"


def test_config_defaults_full_category_set_when_omitted(tmp_path):
    # Arrange
    path = _write(
        tmp_path / "c.yaml",
        f'document_sorter:\n  inbox: "{tmp_path}/in"\n  root: "{tmp_path}/s"\n',
    )
    # Act
    cfg = load_config(path)
    # Assert
    assert "misc" in cfg.categories


def test_config_is_frozen(tmp_path):
    # Arrange
    cfg = load_config(_full_config(tmp_path))
    # Act
    # Assert
    with pytest.raises((AttributeError, TypeError)):
        cfg.inbox = Path("/elsewhere")  # type: ignore[misc]


def test_config_type_is_document_sorter_config(tmp_path):
    # Arrange
    path = _full_config(tmp_path)
    # Act
    cfg = load_config(path)
    # Assert
    assert isinstance(cfg, DocumentSorterConfig)
