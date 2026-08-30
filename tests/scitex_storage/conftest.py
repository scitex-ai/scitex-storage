"""Shared fixtures for the scitex_storage unit tests.

The document-sorter tests need a REAL searchable PDF (embedded text layer),
because that is exactly what the extraction path reads -- and NO MOCKS
(PA-306) means we generate a genuine PDF, not a stub. ``searchable_pdf`` is a
dependency-free factory: it hand-writes a minimal single-page PDF whose
content stream draws the given lines with a standard Helvetica font, so
``scitex_io.load(pdf, mode="text")`` (the production read path) extracts them
back verbatim. Text is Latin-1 (ASCII) -- a hand-rolled simple-font PDF cannot
carry CJK glyphs without a CID font, so the Japanese-classification cases are
tested directly on strings in test__classify.py, while the PDF fixture proves
the end-to-end read path.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _write_searchable_pdf(path: Path, lines: list[str]) -> Path:
    text_ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        if i:
            text_ops.append("0 -16 Td")
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        text_ops.append(f"({esc}) Tj")
    text_ops.append("ET")
    content = ("\n".join(text_ops)).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    n = len(objs) + 1
    out += b"xref\n0 " + str(n).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (
        b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def searchable_pdf():
    """Factory: ``searchable_pdf(path, ["line one", "line two"])`` -> path.

    Writes a genuine single-page PDF with an extractable text layer. Pass
    ``[]`` for a page with no text (the image-only-scan shape)."""
    return _write_searchable_pdf
