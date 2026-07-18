#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Filing: extract -> classify -> archive-before-delete -> file -> index.

This is the source-agnostic core. It consumes :class:`RawDocument` handles
from any :class:`Source` and, for each, reads the embedded text, classifies
it, and files it -- ALWAYS copying the untouched original into the archive
FIRST. That ordering is the whole safety story, exactly as in the package's
``reclaim`` verb: the archive copy exists before the source is moved, so a
crash mid-file leaves a recoverable original, and a wrong classification
costs a move-back rather than data. A misclassification is cheap on purpose,
which is what lets a deterministic keyword classifier ship before it is
perfect.

Every filing appends one JSON line to ``<root>/_index/index.jsonl`` (doc id,
source, category, issuer, date, the archive + sorted paths, sha256, and the
keep-original flag/type). The index is append-only and greppable -- the
substrate a later search UI or a restore reads back.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._classify import classify, detect_keep_original, slugify
from ._config import DocumentSorterConfig
from ._extract import extract_text
from ._sources import RawDocument, Source

_UNDATED_TOKEN = "undated"
_UNDATED_YEAR = "undated"


@dataclass(frozen=True)
class DocumentOutcome:
    """The record of one document's trip through the pipeline.

    Serialised verbatim (via :meth:`to_record`) as one line of the index.
    ``archived_path``/``sorted_path`` are ``None`` for a dry-run (classified,
    nothing moved). ``text_source`` is a COARSE marker of how the text was
    read: ``"io+ocr"`` when extraction ran through scitex-io with the OCR
    fallback enabled, ``"io"`` when text-layer-only. scitex-io does not report
    whether OCR actually fired vs the embedded layer, so this records the
    configured extraction MODE, not an observed embedded-vs-OCR distinction.
    """

    doc_id: str
    source: str
    original_name: str
    category: str
    issuer: str
    date: str | None
    confidence: float
    keep_original: bool
    keep_original_type: str | None
    sha256: str
    archived_path: str | None
    sorted_path: str | None
    text_source: str

    def to_record(self) -> dict:
        return asdict(self)


@dataclass
class RunSummary:
    """Aggregate result of processing one inbox pass -- what the CLI prints."""

    dry_run: bool
    processed: int = 0
    per_category: dict[str, int] = field(default_factory=dict)
    to_misc: int = 0
    keep_original: int = 0
    outcomes: list[DocumentOutcome] = field(default_factory=list)

    def _record(self, outcome: DocumentOutcome) -> None:
        self.processed += 1
        self.per_category[outcome.category] = (
            self.per_category.get(outcome.category, 0) + 1
        )
        if outcome.category == "misc":
            self.to_misc += 1
        if outcome.keep_original:
            self.keep_original += 1
        self.outcomes.append(outcome)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "processed": self.processed,
            "per_category": dict(sorted(self.per_category.items())),
            "to_misc": self.to_misc,
            "keep_original": self.keep_original,
            "outcomes": [o.to_record() for o in self.outcomes],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _target_filename(
    date: str | None, issuer: str, category: str, title: str
) -> str:
    """Deterministic ``YYYY-MM-DD__<issuer>__<category>__<slug>.pdf`` name."""
    date_token = date or _UNDATED_TOKEN
    issuer_slug = slugify(issuer, max_len=24) or "unknown"
    title_slug = slugify(title, max_len=40) or "document"
    return f"{date_token}__{issuer_slug}__{category}__{title_slug}.pdf"


def _title_from_text(text: str) -> str:
    """First non-empty line -- a cheap, deterministic document title."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "document"


def _unique_path(path: Path) -> Path:
    """Return ``path``, or ``name-2.pdf``/``-3`` ... if it already exists.

    Never overwrite an already-filed document -- two scans that classify to
    the same name coexist rather than one clobbering the other.
    """
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 2
    while True:
        candidate = parent / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def process_document(
    raw: RawDocument,
    config: DocumentSorterConfig,
    *,
    text: str | None = None,
) -> DocumentOutcome:
    """Classify and FILE one document, archive-before-delete.

    Steps, in this safety-critical order: (1) read the embedded text (unless
    ``text`` is supplied, e.g. already extracted), (2) classify + detect
    keep-original, (3) hash the original, (4) COPY the untouched original
    into ``config.archive`` FIRST, (5) MOVE the source into
    ``<root>/10_sorted/<category>/<YYYY>/`` under its deterministic name, (6)
    append the index record. The archive copy in step 4 is what makes step 5
    reversible. Returns the :class:`DocumentOutcome` (also appended to the
    index).
    """
    body = extract_text(raw.path, ocr=config.ocr_enabled) if text is None else text
    verdict = classify(body, categories=config.categories)
    ko_type = detect_keep_original(body, config.keep_original)

    sha = _sha256(raw.path)
    doc_id = sha[:16]

    # (4) archive-before-delete: copy the ORIGINAL, untouched, FIRST.
    config.archive.mkdir(parents=True, exist_ok=True)
    archived = _unique_path(config.archive / f"{doc_id}__{raw.path.name}")
    shutil.copy2(raw.path, archived)

    # (5) file the source into 10_sorted/<category>/<YYYY>/<name>.
    year = (verdict.date or "")[:4] if verdict.date else _UNDATED_YEAR
    dest_dir = config.sorted_root / verdict.category / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    title = _title_from_text(body)
    dest = _unique_path(
        dest_dir
        / _target_filename(verdict.date, verdict.issuer, verdict.category, title)
    )
    shutil.move(str(raw.path), str(dest))

    outcome = DocumentOutcome(
        doc_id=doc_id,
        source=raw.source,
        original_name=raw.path.name,
        category=verdict.category,
        issuer=verdict.issuer,
        date=verdict.date,
        confidence=verdict.confidence,
        keep_original=ko_type is not None,
        keep_original_type=ko_type,
        sha256=sha,
        archived_path=str(archived),
        sorted_path=str(dest),
        text_source=_text_source(config),
    )
    _append_index(config.index_path, outcome)
    return outcome


def _text_source(config: DocumentSorterConfig) -> str:
    """Coarse text-provenance marker for the index (see DocumentOutcome)."""
    return "io+ocr" if config.ocr_enabled else "io"


def _classify_only(
    raw: RawDocument, config: DocumentSorterConfig, *, text: str | None = None
) -> DocumentOutcome:
    """Dry-run: classify + hash, move/copy/index NOTHING."""
    body = extract_text(raw.path, ocr=config.ocr_enabled) if text is None else text
    verdict = classify(body, categories=config.categories)
    ko_type = detect_keep_original(body, config.keep_original)
    return DocumentOutcome(
        doc_id=_sha256(raw.path)[:16],
        source=raw.source,
        original_name=raw.path.name,
        category=verdict.category,
        issuer=verdict.issuer,
        date=verdict.date,
        confidence=verdict.confidence,
        keep_original=ko_type is not None,
        keep_original_type=ko_type,
        sha256="",
        archived_path=None,
        sorted_path=None,
        text_source=_text_source(config),
    )


def _append_index(index_path: Path, outcome: DocumentOutcome) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(outcome.to_record(), ensure_ascii=False) + "\n")


def process_inbox(
    source: Source | Iterable[RawDocument],
    config: DocumentSorterConfig,
    *,
    dry_run: bool = False,
) -> RunSummary:
    """Process every document a source yields; return the run summary.

    ``source`` is a :class:`Source` (its ``iter_documents`` is drained) or any
    iterable of :class:`RawDocument` -- the core stays agnostic to origin. On
    ``dry_run`` every document is classified and counted but NOTHING is moved,
    copied, or indexed, so a run can be previewed safely before it touches a
    real scanner folder.
    """
    docs = source.iter_documents() if isinstance(source, Source) else source
    summary = RunSummary(dry_run=dry_run)
    for raw in docs:
        outcome = (
            _classify_only(raw, config)
            if dry_run
            else process_document(raw, config)
        )
        summary._record(outcome)
    return summary


# EOF
