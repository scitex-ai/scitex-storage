#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet-wide storage dashboard — the model + gatherer behind ``fleet-status``.

The operator owns storage across the whole fleet (a WSL workstation, an
HPC login node with two GPFS projects, three NAS units and a MacBook),
and wants to SEE all of it at a glance: every host's space%, inode%,
role/tier and flags, on one page. This module is increment 1 of that
storage-management system — it renders a self-contained HTML dashboard.

TWO HALVES, deliberately split (PA-306: no mocks, ever). The interesting
behaviour is rendering and classification, not I/O, so:

* :func:`build_dashboard_html` (defined in
  :mod:`scitex_storage._render`, re-exported here) is
  **pure** — snapshot dataclasses in, an HTML string out, zero I/O. Every
  threshold / three-state / dark-mode case is exercised by constructing
  plain dataclasses, which is data, not a mock.
* :func:`gather_fleet_snapshot` is the thin I/O layer: one ``statvfs``
  per local path for space, plus :func:`scitex_storage._inodes.probe`
  for inodes. Tested against the real local filesystem only — no
  network, no ssh, no fakes.

THREE-STATE VERDICTS carry straight through from :mod:`._inodes`. A
dashboard that renders "could not read this NAS" as a reassuring green
0% is the exact failure the inode probe was built to avoid — so a
:data:`COULD_NOT_LOOK` row is rendered distinctly (grey, an em dash),
NEVER green, and is counted apart from both healthy and flagged rows in
the header. An unknown is an unknown, not good news.

ROLES come from scitex-dev's shared host registry
(:mod:`scitex_dev.hosts`), which is gaining per-host role/tier attributes
now. :func:`_host_roles` reads them if present and falls back to a small
:data:`DEFAULT_ROLES` map (marking anything it still cannot resolve as
``"?"``) rather than crashing — so this ships today and swaps to the real
registry attribute the moment it lands, with no call-site change.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ._measure._inodes import COULD_NOT_LOOK, MEASURED, NOT_APPLICABLE, probe

#: Percent used at/over which a filesystem row is flagged red on the
#: dashboard — for BOTH space and inodes. Deliberately lower than
#: :data:`scitex_storage._inodes.DEFAULT_WARN_PERCENT` (90): a glance-able
#: dashboard should surface a filesystem that is *getting* full a little
#: earlier than an unattended cron alarms, because a human looking at the
#: board can act before the cliff rather than at it.
FLAG_PERCENT = 85.0

#: Fallback host -> role/tier map, used when the shared host registry
#: (:mod:`scitex_dev.hosts`) is absent or has no role attribute yet. Names
#: match the fleet convention already used across scitex-dev's own docs.
#: A host not found here AND not in the registry renders as ``"?"`` — an
#: honest "unknown", never a guessed tier.
#: NOTE THE TIERS DO NOT FOLLOW THE NUMBERING. scitex-nas-03 is tier1 and
#: scitex-nas-01/02 are tier2, because the 2026-08-07 rename mapped
#: nas->scitex-nas-03, nas1->scitex-nas-01, nas2->scitex-nas-02. Assigning
#: tiers by digit order would silently demote the tier1 unit.
#:
#: The mapping is corroborated by two sources that are independent IN KIND:
#: ssh refuses each retired alias with a message naming its replacement, AND
#: the hardware agrees -- scitex-nas-03 answers `DXP480TPLUS-994`, a UGREEN,
#: matching what the `nas` row below has always recorded as UGREEN /volume1,
#: while scitex-nas-01/02 answer WATANAS1/WATANAS2 against the QNAP rows.
DEFAULT_ROLES: dict[str, str] = {
    "ywata-note-win": "workstation",
    "spartan": "compute/tier1",
    "scitex-nas-03": "tier1",
    "scitex-nas-01": "tier2",
    "scitex-nas-02": "tier2",
    "mba": "workstation",
}

#: Rendered for a role that could not be resolved from any source.
UNKNOWN_ROLE = "?"


@dataclass
class HostStorage:
    """One filesystem row on the dashboard (a host may contribute several).

    ``used_pct`` (space) and ``inode_used_pct`` are ``float | None`` — the
    same discipline as :class:`scitex_storage._inodes.InodeUsage`: ``None``
    means "not a measured number", never "0". ``verdict`` classifies the
    INODE measurement specifically (space is almost always measurable,
    inodes are the metric that goes three-state on a wedged NAS or an APFS
    volume), so a caller reads the verdict to decide how to render the
    inode cell, never inferring it from a numeric zero.
    """

    host: str
    role: str
    mount: str
    verdict: str = MEASURED
    size_bytes: int | None = None
    used_pct: float | None = None
    inode_used_pct: float | None = None
    note: str = ""
    #: Available bytes and source device, used to fold APFS volumes that
    #: SHARE one physical container into a single capacity rather than
    #: counting each volume's copy of the container size. Optional so
    #: rows built before this field (tests, older callers) still work.
    avail_bytes: int | None = None
    source: str = ""

    @property
    def space_flagged(self) -> bool:
        """True when space is MEASURED and at/over :data:`FLAG_PERCENT`."""
        return self.used_pct is not None and self.used_pct >= FLAG_PERCENT

    @property
    def inode_flagged(self) -> bool:
        """True when inodes are MEASURED and at/over :data:`FLAG_PERCENT`.

        ``inode_used_pct is None`` (could-not-look / not-applicable) is
        deliberately NOT flagged: an unknown is not an alarm.
        """
        return self.inode_used_pct is not None and self.inode_used_pct >= FLAG_PERCENT

    @property
    def is_flagged(self) -> bool:
        """Red-row predicate: space OR inodes at/over the threshold."""
        return self.space_flagged or self.inode_flagged

    @property
    def could_not_look(self) -> bool:
        """True when the inode metric could not be read at all."""
        return self.verdict == COULD_NOT_LOOK


@dataclass
class FleetSnapshot:
    """A whole-fleet reading: the rows plus when/how they were gathered."""

    rows: list[HostStorage] = field(default_factory=list)
    generated_at: str = ""
    note: str = ""

    @property
    def total_hosts(self) -> int:
        return len({r.host for r in self.rows})

    @property
    def total_filesystems(self) -> int:
        return len(self.rows)

    @property
    def flagged_count(self) -> int:
        return sum(1 for r in self.rows if r.is_flagged)

    @property
    def could_not_look_count(self) -> int:
        return sum(1 for r in self.rows if r.could_not_look)


# --------------------------------------------------------------------------
# Pure classification helpers (no I/O) — directly unit-testable.
# --------------------------------------------------------------------------


def space_used_pct_from_counts(
    total_blocks: int, free_blocks: int, avail_blocks: int
) -> float | None:
    """``df``-semantics space-used percentage from raw block counts. Pure.

    Matches what ``df`` prints (its "Capacity" column): the denominator is
    what a caller can actually use, not the raw device size, so reserved
    root-only blocks do not read as free space they cannot have. Returns
    ``None`` when the filesystem reports zero usable blocks (a pseudo-fs),
    for the same reason the inode probe returns ``None`` rather than a
    misleading ``0%``.
    """
    if total_blocks <= 0:
        return None
    used = total_blocks - free_blocks
    denom = used + avail_blocks
    if denom <= 0:
        return None
    return (used / denom) * 100.0


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------
# Role registry reader — isolated so it swaps for the real attribute later.
# --------------------------------------------------------------------------


def _host_roles() -> dict[str, str]:
    """Resolve ``{host: role}`` from the shared registry, falling back safely.

    Tries :func:`scitex_dev.hosts.list_hosts` and reads a per-host
    ``role`` attribute (the field being added now); if that attribute is
    not there yet it falls back to the record's ``kind``, and if the
    registry itself is absent it falls back to :data:`DEFAULT_ROLES`. Never
    raises — a dashboard must render even when the registry is mid-build.
    The registry read is confined to this one function precisely so it can
    be replaced by the real role attribute without touching any caller.
    """
    roles: dict[str, str] = dict(DEFAULT_ROLES)
    try:  # pragma: no cover - depends on optional scitex-dev registry
        from scitex_dev.hosts import list_hosts

        for rec in list_hosts():
            role = getattr(rec, "role", None) or getattr(rec, "kind", None)
            if role:
                roles[rec.name] = str(role)
    except Exception:
        # Registry absent, unreadable, or a shape we don't recognise: the
        # DEFAULT_ROLES fallback is a complete, if coarser, answer.
        pass
    return roles


def role_for(host: str, roles: dict[str, str] | None = None) -> str:
    """Role/tier for ``host``, or :data:`UNKNOWN_ROLE` when unresolved."""
    table = roles if roles is not None else _host_roles()
    return table.get(host, UNKNOWN_ROLE)


# --------------------------------------------------------------------------
# I/O layer — one statvfs per local path, plus the inode probe. No network.
# --------------------------------------------------------------------------


def _probe_local_path(path: str, host: str, role: str) -> HostStorage:
    """Measure one LOCAL path: space via statvfs, inodes via the inode probe.

    Never raises for an unreadable path — a wedged mount yields a
    :data:`COULD_NOT_LOOK` row, because a fleet dashboard must be able to
    report "broken" rather than become broken itself.
    """
    inode = probe(path)  # three-state verdict, never raises for bad paths
    size_bytes: int | None = None
    used_pct: float | None = None
    verdict = inode.verdict
    note = inode.detail or ""

    try:
        st = os.statvfs(os.path.expanduser(path))
    except OSError as exc:
        # Space could not be read either: this is unambiguously blind.
        verdict = COULD_NOT_LOOK
        note = f"{exc.__class__.__name__}: {exc.strerror or exc}"
    else:
        size_bytes = st.f_blocks * st.f_frsize
        used_pct = space_used_pct_from_counts(st.f_blocks, st.f_bfree, st.f_bavail)

    return HostStorage(
        host=host,
        role=role,
        mount=path,
        verdict=verdict,
        size_bytes=size_bytes,
        used_pct=_round_or_none(used_pct),
        inode_used_pct=_round_or_none(inode.percent_used),
        note=note,
    )


def gather_fleet_snapshot(paths: list[str] | None = None) -> FleetSnapshot:
    """Build a snapshot from the LOCAL host's filesystems (real statvfs).

    Increment 1 measures only the machine it runs on — one row per
    ``paths`` entry (default: the current user's home and root). Live
    multi-host gathering over ssh is a later increment; the model, the
    renderer and the three-state discipline are all already shaped for it.
    Use :func:`demo_snapshot` for the seeded fleet-wide view.
    """
    host = socket.gethostname()
    role = role_for(host)
    targets = paths if paths else [os.path.expanduser("~"), "/"]
    rows = [_probe_local_path(p, host, role) for p in targets]
    return FleetSnapshot(
        rows=rows,
        generated_at=_now_iso(),
        note=f"Local snapshot of {host} ({len(rows)} filesystem(s)).",
    )


def demo_snapshot() -> FleetSnapshot:
    """Today's real fleet reading, hardcoded so the dashboard demos non-empty.

    These are the actual figures measured across the fleet on 2026-07-18
    (see the card ``storage-tiering-system-gui-20260718``). They exist so
    the board is meaningful before live multi-host gathering is wired, and
    they exercise every rendering case: a flagged space row, a flagged
    inode row, a healthy row, could-not-look inodes, and not-applicable
    (APFS) inodes.
    """
    rows = [
        HostStorage(
            host="ywata-note-win", role="workstation", mount="/",
            verdict=MEASURED, used_pct=96.0, inode_used_pct=19.0,
            note="WSL host root filesystem.",
        ),
        HostStorage(
            host="spartan", role="compute/tier1", mount="punim2354",
            verdict=MEASURED, used_pct=73.0, inode_used_pct=64.0,
            note="GPFS project fileset (5.19M / 8.00M inodes).",
        ),
        HostStorage(
            host="spartan", role="compute/tier1", mount="punim0264",
            verdict=MEASURED, used_pct=71.0, inode_used_pct=97.0,
            note="GPFS project fileset (6,789,784 / 7,000,000 inodes) — inodes near quota.",
        ),
        HostStorage(
            host="scitex-nas-03", role="tier1", mount="/volume1",
            verdict=COULD_NOT_LOOK, used_pct=77.0, inode_used_pct=None,
            note="UGREEN (was `nas`); inode table not exposed over the probe used.",
        ),
        HostStorage(
            host="scitex-nas-01", role="tier2", mount="/share/CACHEDEV1_DATA",
            verdict=COULD_NOT_LOOK, used_pct=63.0, inode_used_pct=None,
            note="QNAP (was `nas1`); inode table not exposed over the probe used.",
        ),
        HostStorage(
            host="scitex-nas-02", role="tier2", mount="/share/CACHEDEV1_DATA",
            verdict=MEASURED, used_pct=27.0, inode_used_pct=2.0,
            note="QNAP (was `nas2`).",
        ),
        HostStorage(
            host="mba", role="workstation", mount="/Volumes/10TB_HDD",
            verdict=NOT_APPLICABLE, used_pct=62.0, inode_used_pct=None,
            note="APFS allocates inodes dynamically — cannot run out.",
        ),
    ]
    return FleetSnapshot(
        rows=rows,
        generated_at=_now_iso(),
        note="Seeded demo snapshot (real fleet figures, 2026-07-18).",
    )


# ``build_dashboard_html`` is re-exported here because this module is its
# documented home, but it LIVES in ``scitex_storage._render`` (grouped with
# the bubble and sunburst renderers, which share its one responsibility).
#
# The re-export is LAZY, via PEP 562, and that is load-bearing rather than
# stylistic. The renderers import the model defined above, so a plain
# bottom-of-file import works only when ``_fleet_status`` is imported
# FIRST: importing ``_render`` first re-enters this module, reaches the
# bottom import, and finds ``_render`` half-built with the name not yet
# bound -- an ImportError that depends on which module the caller happened
# to touch first. Deferring to attribute-access time removes the cycle
# entirely, so both import orders work.
def __getattr__(name: str):  # noqa: D401 - module-level PEP 562 hook
    if name == "build_dashboard_html":
        from ._render import build_dashboard_html

        return build_dashboard_html
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "FLAG_PERCENT",
    "DEFAULT_ROLES",
    "UNKNOWN_ROLE",
    "HostStorage",
    "FleetSnapshot",
    "space_used_pct_from_counts",
    "role_for",
    "gather_fleet_snapshot",
    "demo_snapshot",
    "build_dashboard_html",
]

# EOF
