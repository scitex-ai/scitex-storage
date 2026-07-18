#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load the ``document_sorter`` section of ~/.scitex/storage/config.yaml.

The config is READ, never written, and the loader is a pure function of a
path: it takes the config file location as an argument (defaulting to
:data:`DEFAULT_CONFIG_PATH`) rather than reaching for ``$HOME`` internally,
so a test hands it a tmp file directly — no env sandboxing, no mocks. A
missing file or a missing ``document_sorter`` section fails LOUD with an
actionable message, because a document sorter that silently ran against an
empty/undefined config would file real scans into the wrong place.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Canonical config location on a SciTeX box (already created by the agent).
DEFAULT_CONFIG_PATH = "~/.scitex/storage/config.yaml"

#: Categories used when the config omits an explicit list.
_DEFAULT_CATEGORIES: tuple[str, ...] = (
    "finance",
    "contracts",
    "admin",
    "medical",
    "academic",
    "personal",
    "manuals",
    "misc",
)

#: Filename template when the config omits ``naming``.
_DEFAULT_NAMING = "{date}__{entity}__{type}__{title}"


@dataclass(frozen=True)
class DocumentSorterConfig:
    """Resolved ``document_sorter`` settings, all paths already expanded.

    ``root`` is the destination tree (``10_sorted/``, ``_index/`` live under
    it); ``archive`` is where the untouched original is copied first
    (archive-before-delete). ``categories`` is the closed set a document may
    be filed under (``misc`` is always the safe fallback). ``keep_original``
    lists the physical-document TYPES whose paper original must be kept even
    after scanning (passport, mynumber_card, ...). ``ocr_enabled`` (default
    True — the sorter WANTS OCR on image-only scans) drives the ``ocr=`` flag
    passed to ``scitex_io.load(pdf, mode="text", ocr=...)``: when the embedded
    text layer is empty, scitex-io renders the pages and OCRs them via
    scitex-cv. ``ocr_engine`` / ``ocr_languages`` remain the declared engine
    knobs (scitex-io/scitex-cv default to EasyOCR ja+en).
    """

    inbox: Path
    root: Path
    archive: Path
    categories: tuple[str, ...] = _DEFAULT_CATEGORIES
    naming: str = _DEFAULT_NAMING
    keep_original: tuple[str, ...] = ()
    ocr_enabled: bool = True
    ocr_engine: str = "easyocr"
    ocr_languages: tuple[str, ...] = ("ja", "en")

    @property
    def sorted_root(self) -> Path:
        """``<root>/10_sorted`` — where classified documents are filed."""
        return self.root / "10_sorted"

    @property
    def index_path(self) -> Path:
        """``<root>/_index/index.jsonl`` — the append-only record log."""
        return self.root / "_index" / "index.jsonl"


def _expand(value: str) -> Path:
    return Path(str(value)).expanduser()


def load_config(config_path: str | Path | None = None) -> DocumentSorterConfig:
    """Read and resolve the ``document_sorter`` config from ``config_path``.

    ``config_path=None`` uses :data:`DEFAULT_CONFIG_PATH`. Raises
    ``FileNotFoundError`` (missing file) or ``ValueError`` (no
    ``document_sorter`` section, or a required key absent) with a message
    that names the offending path/key — never a bare ``KeyError`` traceback.
    """
    import yaml

    path = _expand(str(config_path) if config_path is not None else DEFAULT_CONFIG_PATH)
    if not path.is_file():
        raise FileNotFoundError(
            f"storage config not found at {path} -- create it (see "
            "~/.scitex/storage/config.yaml with a `document_sorter:` section) "
            "or pass --config PATH."
        )
    data = yaml.safe_load(path.read_text()) or {}
    section = data.get("document_sorter")
    if not isinstance(section, dict):
        raise ValueError(
            f"config at {path} has no `document_sorter:` section -- nothing to "
            "run. Add one (keys: inbox, root, archive, categories, "
            "keep_original)."
        )

    for required in ("inbox", "root"):
        if not section.get(required):
            raise ValueError(
                f"config at {path} is missing required key "
                f"`document_sorter.{required}`."
            )

    root = _expand(section["root"])
    archive_raw = section.get("archive") or (root / "90_archive")
    ocr = section.get("ocr") or {}
    categories = section.get("categories") or list(_DEFAULT_CATEGORIES)

    return DocumentSorterConfig(
        inbox=_expand(section["inbox"]),
        root=root,
        archive=_expand(str(archive_raw)),
        categories=tuple(categories),
        naming=section.get("naming") or _DEFAULT_NAMING,
        keep_original=tuple(section.get("keep_original") or ()),
        ocr_enabled=bool(ocr.get("enabled", True)),
        ocr_engine=str(ocr.get("engine", "easyocr")),
        ocr_languages=tuple(ocr.get("languages") or ("ja", "en")),
    )


# EOF
