#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_django/_favicon.py
"""scitex-storage's tab favicon: an inline navy SVG ``data:`` URI.

Wired via scitex-ui 0.6.4's ``favicon_href`` context var on
``standalone_shell.html`` (confirmed by reading the installed template
directly: ``{% if favicon_href %}<link rel="icon" href="{{ favicon_href }}">{% endif %}``,
right after ``<title>``). No static asset to ship or 404 on -- matches
figrecipe's inline-SVG-data-URI approach, the shared pattern scitex-ui
is standardizing scholar/storage/writer/todo/figrecipe on.

A simple stacked-disk/database glyph in the SciTeX navy (#1a2744).
"""

from __future__ import annotations

from urllib.parse import quote

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path fill="#1a2744" d="M12 2C7 2 3 3.3 3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5'
    "c0-1.7-4-3-9-3zm0 2c4.4 0 7 1 7 1s-2.6 1-7 1-7-1-7-1 2.6-1 7-1zM5 8.1c1.6"
    ".6 4 1 7 1s5.4-.4 7-1v3.8c-1.6.6-4 1-7 1s-5.4-.4-7-1V8.1zm0 5.8c1.6.6 4 "
    '1 7 1s5.4-.4 7-1v3.8c-1.6.6-4 1-7 1s-5.4-.4-7-1v-3.8z"/></svg>'
)

FAVICON_HREF = "data:image/svg+xml," + quote(_SVG)


# EOF
