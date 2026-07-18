#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage document-intake pipeline — a SOURCE-AGNOSTIC sorter.

This subpackage is the deliberately-separable classification/filing core
behind ``scitex-storage document-sorter``. ScanSnap is ONE input SOURCE, not
the design: a scanner-folder, an email inbox, a phone image-dump all feed the
SAME pipeline by implementing the :class:`Source` interface (``_sources.py``).
The core (:mod:`._extract`, :mod:`._classify`, :mod:`._pipeline`) never knows
where a document came from — a new source drops in without touching it.

The MVP is deterministic and testable end-to-end, with no LLM and no
image-OCR: it reads a searchable PDF's EMBEDDED text layer (ScanSnap's
OCR-on setting writes one), classifies by a small extensible keyword+regex
table, and files with the same archive-before-delete reversibility as the
package's ``reclaim`` verb — the original is copied to the archive FIRST, so
a wrong classification costs a move-back, never data. An image-only-PDF
EasyOCR fallback is a documented TODO (see :mod:`._extract`), not this MVP.

Public names are re-exported here so ``scitex_storage``'s top-level lazy
loader can reach them via a single submodule row.
"""

from __future__ import annotations

from ._classify import (
    CATEGORY_KEYWORDS,
    KEEP_ORIGINAL_KEYWORDS,
    Classification,
    classify,
    detect_keep_original,
    extract_date,
)
from ._config import (
    DEFAULT_CONFIG_PATH,
    DocumentSorterConfig,
    load_config,
)
from ._extract import extract_text
from ._pipeline import (
    DocumentOutcome,
    RunSummary,
    process_document,
    process_inbox,
)
from ._sources import RawDocument, ScanSnapFolderSource, Source

__all__ = [
    "CATEGORY_KEYWORDS",
    "KEEP_ORIGINAL_KEYWORDS",
    "Classification",
    "classify",
    "detect_keep_original",
    "extract_date",
    "DEFAULT_CONFIG_PATH",
    "DocumentSorterConfig",
    "load_config",
    "extract_text",
    "DocumentOutcome",
    "RunSummary",
    "process_document",
    "process_inbox",
    "RawDocument",
    "ScanSnapFolderSource",
    "Source",
]

# EOF
