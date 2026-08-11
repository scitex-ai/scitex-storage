#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_measure/__init__.py
"""Measure a tree and form a verdict about it — never change it.

Ten modules: the read-only probes (``_scan``, ``_survey``, ``_space``,
``_inodes``, ``_duplicates``, ``_open_handles``) and the judgement modules that
consume them (``_classify``, ``_regenerable``, ``_redundancy``,
``_accounting``). Nothing in this package writes to the tree it is measuring.
That invariant is the package boundary, and it is why ``_reclaim`` lives in
``_transfer/`` instead: it acts.

NAMED ``_measure`` RATHER THAN ``_survey``, deliberately. ``_survey.py`` is one
of the ten modules, and a package sharing a name with a module inside it is the
ambiguity the naming rule exists to kill — you would have to say "the survey in
survey" every time you referred to either. ``_measure`` names the intent;
``_survey`` is one activity within it.

Deliberately NO re-exports, matching ``_transfer/``. Importers name the module
they depend on, so the dependency stays legible at the import site.
"""
