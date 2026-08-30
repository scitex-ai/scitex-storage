#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_transfer/__init__.py
"""Move data to a destination and confirm it arrived.

One responsibility, nine modules: every verb here either RELOCATES bytes or
proves that a relocation landed. That is the line separating this package from
``_measure/`` — measurement forms a verdict about a tree and changes nothing;
transfer changes something and must then prove what it changed.

``_reclaim`` belongs here rather than in ``_measure/`` despite its association
with the derived/precious classifier: it MOVES things. Whatever chose the paths
is the caller's concern, handed in as an argument.

Deliberately NO re-exports. Importers name the module they depend on
(``from ._transfer._archive import ...``), so the dependency stays legible at
the import site. A re-export layer here would hide which module a caller
actually needs and would have to be maintained in lockstep with all nine.
"""
