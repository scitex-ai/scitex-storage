#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read the embedded text layer of a searchable PDF.

ScanSnap (and most scanners) with OCR-on embed a real text layer in the
PDF. Reading that layer is a cheap, deterministic, dependency-light
extraction — no OCR pass, no image processing — which is why the MVP
targets it first. :func:`extract_text` is a PURE function of a path: same
bytes in, same string out, no I/O beyond the read, nothing global touched.

DELIBERATELY OUT OF SCOPE (documented TODO, not this MVP): an image-only PDF
(scanned with OCR OFF, or a photo) has NO text layer, so this returns an
empty string for it rather than guessing. The planned fallback is a second
EasyOCR pass (already on the host, ja+en) gated on "extracted text is empty
/ too short" — see the ``document_sorter.ocr`` config block. The pipeline
already routes an empty extraction to ``misc`` (never a silent wrong guess),
so wiring that fallback is additive and safe to defer.
"""

from __future__ import annotations

from pathlib import Path


def extract_text(pdf_path: str | Path) -> str:
    """Return the concatenated embedded text of every page in ``pdf_path``.

    Uses ``pypdf`` (pure-Python, no system binary). Pages are joined with a
    blank line. A page with no text layer contributes an empty string rather
    than raising, so an image-only PDF yields ``""`` (which the classifier
    routes to ``misc``). Raises ``FileNotFoundError`` for a missing path and
    lets a genuinely corrupt PDF raise from ``pypdf`` -- a file that cannot
    be read is a loud failure, not a silently-empty extraction.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"no PDF to extract at {path}")

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts).strip()


# EOF
