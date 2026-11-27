#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_tiers.py
"""Canonical destination-tier facts: which storage tier a host belongs to.

ONE module owns the NAS-unit tier mapping so the archive manifest, the GUI
and fleet views cannot drift into three private copies of it. The mapping
itself is transcribed from measured hosts (see _fleet_status.DEFAULT_ROLES
for the corroboration trail: ssh-refused retired aliases + answering
hardware identities), not inferred from the numbering -- scitex-nas-03 is
tier1 and 01/02 are tier2, because the 2026-08-07 rename mapped
nas->scitex-nas-03, nas1->scitex-nas-01, nas2->scitex-nas-02.

Deliberately NOT a role registry: fleet roles ("workstation",
"compute/tier1") stay in :mod:`scitex_storage._fleet_status`, and the
shared host registry (scitex-dev hosts) stays authoritative wherever it
exists. This module answers one question -- "what tier does an archive
destination belong to" -- with an honest "unknown" for anything else,
never a guess.
"""

from __future__ import annotations

from ._transfer._archive_transport import DESTINATIONS, RETIRED_DESTINATIONS

#: Canonical tier per LIVE archive destination.
TIERS: dict[str, str] = {
    "scitex-nas-03": "tier1",
    "scitex-nas-01": "tier2",
    "scitex-nas-02": "tier2",
}

#: Rendered when a destination belongs to no known tier.
UNKNOWN_TIER = "unknown"


def tier_of_destination(destination: str) -> str:
    """Tier for an archive ``destination`` alias, live or retired.

    Retired aliases resolve first (same rewrite ``plan_archive`` applies),
    so callers pass what the user typed. Unknown names return
    :data:`UNKNOWN_TIER` -- recording "unknown" beats recording a guess on
    a manifest that gates an irreversible delete.
    """
    live = RETIRED_DESTINATIONS.get(destination, destination)
    if live not in DESTINATIONS:
        return UNKNOWN_TIER
    return TIERS.get(live, UNKNOWN_TIER)
