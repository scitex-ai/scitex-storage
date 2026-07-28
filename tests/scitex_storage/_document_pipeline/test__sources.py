"""Unit tests for the source-plugin interface + ScanSnapFolderSource.

NO MOCKS (PA-306): real files in a real tmp inbox. ONE assertion per test
(PA-307), AAA-structured.
"""

import pytest

from scitex_storage._document_pipeline._sources import (
    RawDocument,
    ScanSnapFolderSource,
    Source,
)


def _pdf(inbox, name):
    inbox.mkdir(parents=True, exist_ok=True)
    p = inbox / name
    p.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return p


def test_scansnap_source_yields_the_pdf(tmp_path):
    # Arrange
    _pdf(tmp_path / "inbox", "a.pdf")
    # Act
    docs = list(ScanSnapFolderSource(tmp_path / "inbox").iter_documents())
    # Assert
    assert len(docs) == 1


def test_scansnap_source_yields_raw_document(tmp_path):
    # Arrange
    _pdf(tmp_path / "inbox", "a.pdf")
    # Act
    docs = list(ScanSnapFolderSource(tmp_path / "inbox").iter_documents())
    # Assert
    assert isinstance(docs[0], RawDocument)


def test_scansnap_source_sets_source_name(tmp_path):
    # Arrange
    _pdf(tmp_path / "inbox", "a.pdf")
    # Act
    docs = list(ScanSnapFolderSource(tmp_path / "inbox").iter_documents())
    # Assert
    assert docs[0].source == "scansnap-folder"


def test_scansnap_source_skips_non_pdf(tmp_path):
    # Arrange
    inbox = tmp_path / "inbox"
    _pdf(inbox, "a.pdf")
    (inbox / "note.txt").write_text("hi")
    # Act
    docs = list(ScanSnapFolderSource(inbox).iter_documents())
    # Assert
    assert len(docs) == 1


def test_scansnap_source_skips_hidden(tmp_path):
    # Arrange
    inbox = tmp_path / "inbox"
    _pdf(inbox, "a.pdf")
    _pdf(inbox, ".hidden.pdf")
    # Act
    docs = list(ScanSnapFolderSource(inbox).iter_documents())
    # Assert
    assert len(docs) == 1


def test_scansnap_source_recurses_subfolders(tmp_path):
    # Arrange
    inbox = tmp_path / "inbox"
    _pdf(inbox / "profile1", "a.pdf")
    # Act
    docs = list(ScanSnapFolderSource(inbox).iter_documents())
    # Assert
    assert len(docs) == 1


def test_scansnap_source_missing_inbox_yields_nothing(tmp_path):
    # Arrange
    inbox = tmp_path / "does-not-exist"
    # Act
    docs = list(ScanSnapFolderSource(inbox).iter_documents())
    # Assert
    assert docs == []


def test_scansnap_source_yields_sorted(tmp_path):
    # Arrange
    inbox = tmp_path / "inbox"
    _pdf(inbox, "b.pdf")
    _pdf(inbox, "a.pdf")
    # Act
    names = [d.path.name for d in ScanSnapFolderSource(inbox).iter_documents()]
    # Assert
    assert names == ["a.pdf", "b.pdf"]


def test_source_is_abstract(tmp_path):
    # Arrange
    del tmp_path
    # Act
    # Assert
    with pytest.raises(TypeError):
        Source()  # type: ignore[abstract]


def test_raw_document_carries_source(tmp_path):
    # Arrange
    del tmp_path
    # Act
    doc = RawDocument(path=None, source="email")  # type: ignore[arg-type]
    # Assert
    assert doc.source == "email"
