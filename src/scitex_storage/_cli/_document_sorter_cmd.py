#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage document-sorter run`` — one-shot inbox intake.

Reads the ``document_sorter`` config, drains the configured SOURCE
(ScanSnap folder, in this MVP), and for each searchable PDF: extracts the
embedded text, classifies it deterministically, and files it with
archive-before-delete. ``run`` is a single pass (no daemon/watch verb in
this MVP -- that waits on scitex-dev's dev-group service template). The
extract/classify/file logic lives entirely in ``.._document_pipeline`` so
future sources (email, scanner) reuse it untouched; this leaf is only the
CLI shell.
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs, spec_group_kwargs


@click.group(
    "document-sorter",
    **spec_group_kwargs(
        summary="Sort scanned documents (searchable PDFs) into a tidy tree.",
        description=(
            "A source-agnostic document-intake pipeline. ScanSnap is one "
            "input source; email/scanner sources plug into the same core "
            "later. `run` processes the configured inbox once: read each "
            "PDF's embedded OCR text, classify by date + keyword rules, and "
            "file into <root>/10_sorted/<category>/<YYYY>/ -- always copying "
            "the untouched original into the archive first, so a wrong "
            "classification costs a move-back, never data.",
        ),
    ),
)
def document_sorter_group() -> None:
    pass


@document_sorter_group.command(
    "run",
    **spec_command_kwargs(
        summary="Process the inbox once: classify + file every scanned PDF.",
        description=(
            "One-shot pass over the configured ScanSnap inbox. For each "
            "searchable PDF, reads its embedded text layer, classifies "
            "(category via keyword rules, date via ja/ISO regex, issuer via "
            "heuristics), copies the ORIGINAL into the archive FIRST "
            "(reversible), then moves the scan into "
            "<root>/10_sorted/<category>/<YYYY>/ under a deterministic "
            "YYYY-MM-DD__issuer__category__title.pdf name and appends a "
            "record to <root>/_index/index.jsonl. Low-confidence or "
            "unmatched documents go to `misc` -- never a silent wrong guess. "
            "--dry-run classifies and reports without moving anything."
        ),
        examples=(
            ("{prog} document-sorter run", "process the inbox once"),
            ("{prog} document-sorter run --dry-run", "preview classifications, move nothing"),
            ("{prog} document-sorter run --config ./my.yaml", "use a specific config"),
            ("{prog} document-sorter run --json", "machine-readable run summary"),
        ),
    ),
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to the storage config yaml (default: ~/.scitex/storage/config.yaml).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Classify and report only; move, copy, and index nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def document_sorter_run_cmd(
    config_path: str | None, dry_run: bool, as_json: bool
) -> None:
    from .._document_pipeline import (
        ScanSnapFolderSource,
        load_config,
        process_inbox,
    )

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    source = ScanSnapFolderSource(config.inbox)
    summary = process_inbox(source, config, dry_run=dry_run)

    if as_json:
        click.echo(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
    else:
        click.echo(_format_summary(summary))


def _format_summary(summary) -> str:
    head = "document-sorter run" + ("  (dry-run -- nothing moved)" if summary.dry_run else "")
    lines = [head, "=" * len(head)]
    lines.append(f"  processed:      {summary.processed}")
    if summary.per_category:
        lines.append("  by category:")
        for cat, n in sorted(summary.per_category.items()):
            lines.append(f"    {cat:<12} {n}")
    lines.append(f"  -> misc:        {summary.to_misc}")
    lines.append(f"  keep-original:  {summary.keep_original}")
    if summary.processed == 0:
        lines.append("")
        lines.append("  (inbox empty -- nothing to sort)")
    return "\n".join(lines)


# EOF
