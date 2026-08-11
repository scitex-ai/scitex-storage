#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract a PDF's text, via scitex-io (embedded layer, with OCR fallback).

Reading is DELEGATED to ``scitex_io.load(pdf, mode="text", ocr=...)`` rather
than driving a PDF library here: scitex-io owns PDF reading for the whole
ecosystem now, and dogfooding it is the point of this phase. With ``ocr=True``
scitex-io reads the embedded text layer first and, only when that layer is
empty, renders the pages and OCRs them through ``scitex_cv.ocr`` (EasyOCR,
ja+en) -- so a ScanSnap PDF scanned WITH OCR-on returns its embedded text with
no OCR pass, while an image-only scan (OCR off, or a photo) is no longer a
silent empty string. ``ocr=False`` restores the text-layer-only behaviour.

:func:`extract_text` stays a PURE function of ``(path, ocr)``: same bytes in,
same string out, no global state touched. An empty extraction (image-only PDF
with OCR disabled, or scitex-cv unavailable) returns ``""``, which the
classifier routes to ``misc`` -- never a silent wrong guess.

THE OCR STACK IS OPTIONAL AND IS NOT DECLARED HERE, on purpose. scitex-io's
OCR path rasterises pages with PyMuPDF and recognises them with
``scitex-cv[ocr]``; it RAISES ``ImportError`` when either is missing rather
than degrading. PyMuPDF is AGPL-3.0, so pulling it into storage's base install
would hand every consumer a copyleft obligation to get a fallback most of them
never reach. So we ask for OCR, and treat its absence as the documented
empty-extraction case -- see :func:`extract_text` for how that is kept
distinct from "no PDF backend at all", which stays loud.
"""

from __future__ import annotations

from pathlib import Path


def extract_text(pdf_path: str | Path, *, ocr: bool = True) -> str:
    """Return the text of ``pdf_path`` via ``scitex_io.load(mode="text")``.

    ``ocr`` (default True) is forwarded to scitex-io: when the embedded text
    layer is empty it renders + OCRs the pages via scitex-cv; when False it
    reads the text layer only. Raises ``FileNotFoundError`` for a missing path
    (a file that cannot be read is a loud failure, not a silently-empty
    extraction). Returns ``""`` when the PDF genuinely yields no text.

    A MISSING OCR STACK DEGRADES; A MISSING PDF BACKEND DOES NOT. scitex-io
    raises ``ImportError`` for both, so we tell them apart by RETRYING with
    ``ocr=False``: if the text-layer read then succeeds, only the OCR extras
    were missing and the documented ``""`` is correct. If it raises too, no
    PDF backend is installed at all -- a packaging fault, not an empty
    document -- and the ORIGINAL error propagates. Retrying rather than
    matching on the message keeps this working when the wording changes; an
    error string is not an API.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"no PDF to extract at {path}")

    import scitex_io

    try:
        text = scitex_io.load(str(path), mode="text", ocr=ocr)
    except ImportError:
        if not ocr:
            raise
        text = scitex_io.load(str(path), mode="text", ocr=False)
    if not text:
        return ""
    return str(text).strip()


# EOF
