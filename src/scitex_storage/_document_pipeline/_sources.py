#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pluggable document SOURCES — the source-agnostic entry to the pipeline.

A :class:`Source` yields :class:`RawDocument` handles from somewhere an
inbox accumulates raw scans. The pipeline core consumes ``RawDocument``
objects and NEVER asks where they came from, so a new origin (email
attachments, a network scanner drop, a phone image-dump) plugs in by
subclassing :class:`Source` and implementing one method — no change to
extraction, classification, or filing.

THE CONTRACT for a new source author (this is the whole extension surface):

* Subclass :class:`Source`, set a stable ``name`` (goes into every index
  record's ``source`` field, so keep it short and slug-safe, e.g.
  ``"email"``), and implement ``iter_documents() -> Iterator[RawDocument]``.
* Each yielded ``RawDocument`` MUST point at a real local file the pipeline
  can read and MOVE (the filing step relocates it). A source that fetches
  from a remote (email/IMAP) is responsible for landing the bytes in a local
  spool dir first and yielding that local path -- the core does no network.
* Yield in a STABLE order (sort) so a dry-run and the real run agree, and so
  two runs over the same inbox are reproducible.
* Yield each document ONCE per run. De-duplication across runs is the
  pipeline's job (it hashes content), not the source's.

Only :class:`ScanSnapFolderSource` is implemented here; email/scanner are
intentionally left for later authors to add against this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawDocument:
    """One raw document handed to the pipeline: a local path + its origin.

    ``path`` is a real, readable local file (the filing step will move it).
    ``source`` is the yielding :class:`Source`'s ``name``, recorded verbatim
    in the index so a document's provenance is never lost.
    """

    path: Path
    source: str


class Source(ABC):
    """Abstract origin of raw documents. See the module docstring contract."""

    #: Stable, slug-safe identifier recorded as each document's ``source``.
    name: str = "source"

    @abstractmethod
    def iter_documents(self) -> Iterator[RawDocument]:
        """Yield each raw document exactly once, in a stable (sorted) order."""
        raise NotImplementedError


class ScanSnapFolderSource(Source):
    """Yield PDFs a ScanSnap drops into a watched inbox folder.

    Reads ``config.document_sorter.inbox`` (a Windows ScanSnap Home output
    dir surfaced under WSL ``/mnt/c/...``). Walks the folder for ``*.pdf``
    files (recursively, ScanSnap Home nests by profile), skips hidden/temp
    files, and yields them sorted for reproducibility. A missing inbox yields
    nothing rather than raising -- an empty scanner folder is a normal state,
    not an error (the CLI reports "0 processed").
    """

    name = "scansnap-folder"

    #: File extensions this source treats as documents.
    extensions: tuple[str, ...] = (".pdf",)

    def __init__(self, inbox: str | Path):
        self.inbox = Path(inbox)

    def iter_documents(self) -> Iterator[RawDocument]:
        if not self.inbox.is_dir():
            return
        exts = {e.lower() for e in self.extensions}
        for path in sorted(self.inbox.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix.lower() not in exts:
                continue
            yield RawDocument(path=path, source=self.name)


# EOF
