#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_render/__init__.py
"""HTML renderers for the fleet views -- pure functions, no I/O.

Three ways to look at the same :class:`~scitex_storage._fleet_status.FleetSnapshot`:

* :func:`build_dashboard_html` -- the table, one row per filesystem, with
  space/inode donuts and three-state verdicts.
* :func:`build_bubbles_html` -- one circle per host, AREA proportional to
  capacity, ring fill to usage.
* :func:`build_sunburst_html` -- the Codecov-style nested radial: inner
  ring hosts, outer ring filesystems, angle by capacity, colour by the
  selected metric (space% or inode%).

Grouped as a subpackage because they share one responsibility (turning a
snapshot into a self-contained HTML page), not merely a name prefix --
the project audit explicitly rejects blind prefix-promotion. Every one of
them is PURE and emits a document with no external assets, so a saved
page opens offline.
"""

from __future__ import annotations

from ._bubbles import aggregate_hosts, build_bubbles_html
from ._fleet_table import build_dashboard_html
from ._sunburst import build_hierarchy, build_sunburst_html

__all__ = [
    "aggregate_hosts",
    "build_bubbles_html",
    "build_dashboard_html",
    "build_hierarchy",
    "build_sunburst_html",
]

# EOF
