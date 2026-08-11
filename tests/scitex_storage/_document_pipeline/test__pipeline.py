"""Unit tests for the filing pipeline (extract -> classify -> archive -> file).

NO MOCKS (PA-306): a real config (constructed with tmp_path dirs, so no env),
real searchable PDFs, real moves/copies/index writes. The properties pinned
hardest are ARCHIVE-BEFORE-DELETE (the original survives in the archive) and
DRY-RUN (nothing moves). ONE assertion per test (PA-307), AAA-structured.
"""

import json
from pathlib import Path

from scitex_storage._document_pipeline._config import DocumentSorterConfig
from scitex_storage._document_pipeline._pipeline import (
    RunSummary,
    process_document,
    process_inbox,
)
from scitex_storage._document_pipeline._sources import RawDocument

FINANCE_LINES = ["INVOICE No. 4412", "payment received", "Date 2026-07-18"]
PASSPORT_LINES = ["PASSPORT", "No. A1234567"]
UNMATCHED_LINES = ["random words with nothing", "recognisable at all here"]


def _config(tmp_path: Path, *, keep_original=()) -> DocumentSorterConfig:
    return DocumentSorterConfig(
        inbox=tmp_path / "inbox",
        root=tmp_path / "scans",
        archive=tmp_path / "scans" / "90_archive",
        keep_original=tuple(keep_original),
    )


def _raw(tmp_path, searchable_pdf, name, lines) -> RawDocument:
    pdf = searchable_pdf(tmp_path / "inbox" / name, lines)
    return RawDocument(path=pdf, source="scansnap-folder")


# --------------------------------------------------------------------------
# process_document -- the archive-before-delete filing.
# --------------------------------------------------------------------------


def test_process_document_writes_archive_copy(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert Path(outcome.archived_path).is_file()


def test_process_document_archive_copy_matches_original(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    original_bytes = raw.path.read_bytes()
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert Path(outcome.archived_path).read_bytes() == original_bytes


def test_process_document_moves_source_out_of_inbox(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    process_document(raw, cfg)
    # Assert
    assert not raw.path.exists()


def test_process_document_files_into_category(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert "/10_sorted/finance/" in outcome.sorted_path.replace("\\", "/")


def test_process_document_files_under_year(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert "/2026/" in outcome.sorted_path.replace("\\", "/")


def test_process_document_filename_carries_category(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert "__finance__" in Path(outcome.sorted_path).name


def test_process_document_filename_starts_with_date(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert Path(outcome.sorted_path).name.startswith("2026-07-18__")


def test_process_document_lands_a_real_file(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert Path(outcome.sorted_path).is_file()


def test_process_document_records_sha256(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert len(outcome.sha256) == 64


def test_process_document_records_text_source(tmp_path, searchable_pdf):
    # Arrange -- default config has ocr_enabled=True.
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert outcome.text_source == "io+ocr"


def test_process_document_index_carries_text_source(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    process_document(raw, cfg)
    record = json.loads(cfg.index_path.read_text().splitlines()[0])
    # Assert
    assert record["text_source"] == "io+ocr"


def test_process_document_appends_one_index_line(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    process_document(raw, cfg)
    # Assert
    assert cfg.index_path.read_text().count("\n") == 1


def test_process_document_index_line_is_valid_json(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    outcome = process_document(raw, cfg)
    record = json.loads(cfg.index_path.read_text().splitlines()[0])
    # Assert
    assert record["doc_id"] == outcome.doc_id


def test_process_document_keep_original_flag_set(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path, keep_original=["passport"])
    raw = _raw(tmp_path, searchable_pdf, "p.pdf", PASSPORT_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert outcome.keep_original is True


def test_process_document_keep_original_records_type(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path, keep_original=["passport"])
    raw = _raw(tmp_path, searchable_pdf, "p.pdf", PASSPORT_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert outcome.keep_original_type == "passport"


def test_process_document_unmatched_goes_to_misc(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "u.pdf", UNMATCHED_LINES)
    # Act
    outcome = process_document(raw, cfg)
    # Assert
    assert outcome.category == "misc"


def test_process_document_collision_keeps_both(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    process_document(_raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES), cfg)
    # Act
    process_document(_raw(tmp_path, searchable_pdf, "b.pdf", FINANCE_LINES), cfg)
    filed = list((cfg.sorted_root / "finance" / "2026").iterdir())
    # Assert
    assert len(filed) == 2


# --------------------------------------------------------------------------
# process_inbox -- the run over a source, with dry-run.
# --------------------------------------------------------------------------


def test_process_inbox_counts_processed(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    docs = [
        _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES),
        _raw(tmp_path, searchable_pdf, "b.pdf", UNMATCHED_LINES),
    ]
    # Act
    summary = process_inbox(docs, cfg)
    # Assert
    assert summary.processed == 2


def test_process_inbox_per_category_counts(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    docs = [
        _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES),
        _raw(tmp_path, searchable_pdf, "u.pdf", UNMATCHED_LINES),
    ]
    # Act
    summary = process_inbox(docs, cfg)
    # Assert
    assert summary.per_category.get("finance") == 1


def test_process_inbox_counts_misc(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    docs = [_raw(tmp_path, searchable_pdf, "u.pdf", UNMATCHED_LINES)]
    # Act
    summary = process_inbox(docs, cfg)
    # Assert
    assert summary.to_misc == 1


def test_process_inbox_dry_run_moves_nothing(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    process_inbox([raw], cfg, dry_run=True)
    # Assert
    assert raw.path.exists()


def test_process_inbox_dry_run_writes_no_index(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    process_inbox([raw], cfg, dry_run=True)
    # Assert
    assert not cfg.index_path.exists()


def test_process_inbox_dry_run_still_classifies(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    summary = process_inbox([raw], cfg, dry_run=True)
    # Assert
    assert summary.outcomes[0].category == "finance"


def test_process_inbox_empty_source_is_zero(tmp_path):
    # Arrange
    cfg = _config(tmp_path)
    # Act
    summary = process_inbox([], cfg)
    # Assert
    assert summary.processed == 0


def test_run_summary_to_dict_has_processed(tmp_path, searchable_pdf):
    # Arrange
    cfg = _config(tmp_path)
    raw = _raw(tmp_path, searchable_pdf, "a.pdf", FINANCE_LINES)
    # Act
    summary = process_inbox([raw], cfg)
    # Assert
    assert summary.to_dict()["processed"] == 1


def test_run_summary_type(tmp_path):
    # Arrange
    cfg = _config(tmp_path)
    # Act
    summary = process_inbox([], cfg)
    # Assert
    assert isinstance(summary, RunSummary)
