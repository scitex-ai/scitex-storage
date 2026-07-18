"""Unit tests for `scitex-storage document-sorter run`.

NO MOCKS (PA-306): a real config yaml + real searchable PDFs in a tmp inbox,
driven through the real CLI with CliRunner. The config PATH is passed with
--config, so there is no env to sandbox and no monkeypatch. ONE assertion
per test (PA-307), AAA-structured.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from scitex_storage._cli import main

FINANCE_LINES = ["INVOICE No. 4412", "payment received", "Date 2026-07-18"]


def _write_config(tmp_path: Path) -> Path:
    text = (
        "document_sorter:\n"
        f'  inbox: "{tmp_path / "inbox"}"\n'
        f'  root: "{tmp_path / "scans"}"\n'
        f'  archive: "{tmp_path / "scans" / "90_archive"}"\n'
        "  categories: [finance, contracts, admin, medical, academic, "
        "personal, manuals, misc]\n"
        "  keep_original: [passport]\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    return cfg


def _seed_inbox(tmp_path, searchable_pdf, name="a.pdf", lines=FINANCE_LINES):
    return searchable_pdf(tmp_path / "inbox" / name, lines)


def test_run_exits_clean(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    result = CliRunner().invoke(main, ["document-sorter", "run", "--config", str(cfg)])
    # Assert
    assert result.exit_code == 0


def test_run_moves_source_out_of_inbox(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    pdf = _seed_inbox(tmp_path, searchable_pdf)
    # Act
    CliRunner().invoke(main, ["document-sorter", "run", "--config", str(cfg)])
    # Assert
    assert not pdf.exists()


def test_run_files_into_sorted_tree(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    CliRunner().invoke(main, ["document-sorter", "run", "--config", str(cfg)])
    filed = list((tmp_path / "scans" / "10_sorted" / "finance" / "2026").iterdir())
    # Assert
    assert len(filed) == 1


def test_run_reports_processed_count(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    result = CliRunner().invoke(main, ["document-sorter", "run", "--config", str(cfg)])
    # Assert
    assert "processed:      1" in result.output


def test_run_dry_run_leaves_source(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    pdf = _seed_inbox(tmp_path, searchable_pdf)
    # Act
    CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(cfg), "--dry-run"]
    )
    # Assert
    assert pdf.exists()


def test_run_dry_run_says_nothing_moved(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    result = CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(cfg), "--dry-run"]
    )
    # Assert
    assert "dry-run" in result.output


def test_run_json_is_parseable(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    result = CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(cfg), "--json"]
    )
    # Assert
    assert json.loads(result.output)["processed"] == 1


def test_run_json_reports_category(tmp_path, searchable_pdf):
    # Arrange
    cfg = _write_config(tmp_path)
    _seed_inbox(tmp_path, searchable_pdf)
    # Act
    result = CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(cfg), "--json"]
    )
    # Assert
    assert json.loads(result.output)["per_category"].get("finance") == 1


def test_run_missing_config_is_clean_error(tmp_path):
    # Arrange
    missing = tmp_path / "nope.yaml"
    # Act
    result = CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(missing)]
    )
    # Assert
    assert result.exit_code != 0


def test_run_missing_config_no_traceback(tmp_path):
    # Arrange
    missing = tmp_path / "nope.yaml"
    # Act
    result = CliRunner().invoke(
        main, ["document-sorter", "run", "--config", str(missing)]
    )
    # Assert
    assert "Traceback" not in result.output


def test_run_empty_inbox_processes_zero(tmp_path):
    # Arrange
    cfg = _write_config(tmp_path)
    (tmp_path / "inbox").mkdir()
    # Act
    result = CliRunner().invoke(main, ["document-sorter", "run", "--config", str(cfg)])
    # Assert
    assert "processed:      0" in result.output


def test_document_sorter_group_is_registered(tmp_path):
    # Arrange
    del tmp_path
    # Act
    result = CliRunner().invoke(main, ["--help"])
    # Assert
    assert "document-sorter" in result.output
