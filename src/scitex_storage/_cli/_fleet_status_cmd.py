#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage fleet-status`` — render a fleet storage dashboard to HTML.

Increment 1 of the storage-management system: a self-contained,
dark-mode HTML page showing every host's space%, inode% and three-state
verdict at a glance. Writes to
``~/.scitex/scitex-storage/runtime/fleet-status.html`` by default (the same
runtime tree as ``archive`` manifests and the GUI state), with ``--output``
to override and ``--json`` for the machine-readable snapshot.

``--demo`` renders a seeded fleet-wide snapshot (real figures measured
across the fleet) so the board is meaningful before live multi-host
gathering over ssh is wired; without it the command measures the LOCAL
host's filesystems via ``statvfs`` + the inode probe.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .._fleet_status import (
    FleetSnapshot,
    HostStorage,
    build_dashboard_html,
    demo_snapshot,
    gather_fleet_snapshot,
)
from ._compat import spec_command_kwargs


def _default_output() -> Path:
    """Resolved fresh on every call (not a module constant) so tests can
    sandbox it via ``$HOME`` — matching ``_archive._manifest_dir``."""
    return Path("~/.scitex/scitex-storage/runtime/fleet-status.html").expanduser()


def _row_dict(row: HostStorage) -> dict:
    return {
        "host": row.host,
        "role": row.role,
        "mount": row.mount,
        "verdict": row.verdict,
        "size_bytes": row.size_bytes,
        "used_pct": row.used_pct,
        "inode_used_pct": row.inode_used_pct,
        "flagged": row.is_flagged,
        "note": row.note,
    }


def _snapshot_dict(snapshot: FleetSnapshot) -> dict:
    return {
        "generated_at": snapshot.generated_at,
        "note": snapshot.note,
        "total_hosts": snapshot.total_hosts,
        "total_filesystems": snapshot.total_filesystems,
        "flagged_count": snapshot.flagged_count,
        "could_not_look_count": snapshot.could_not_look_count,
        "rows": [_row_dict(r) for r in snapshot.rows],
    }


@click.command(
    "fleet-status",
    **spec_command_kwargs(
        summary="Render a fleet-wide storage dashboard (space% + inode% + verdicts) to HTML.",
        description=(
            "Builds a self-contained, dark-mode HTML page grouping every "
            "host's filesystems by role/tier, with a used-% bar for space and "
            "for inodes and a three-state verdict (measured / not-applicable / "
            "could-not-look). Rows at or over 85% space OR inodes are flagged "
            "red; a filesystem that could not be read is rendered grey with an "
            "em dash and is NEVER shown as a reassuring green 0%. Writes to "
            "~/.scitex/scitex-storage/runtime/fleet-status.html by default. "
            "Increment 1 measures the LOCAL host; --demo renders a seeded "
            "fleet-wide snapshot so the board is non-empty before live "
            "multi-host gathering is wired.",
        ),
        examples=(
            ("{prog} fleet-status --demo", "seeded fleet-wide board to the default path"),
            ("{prog} fleet-status", "measure THIS host's filesystems"),
            ("{prog} fleet-status --demo --output /tmp/board.html", "write elsewhere"),
            ("{prog} fleet-status --demo --json", "machine-readable snapshot"),
        ),
    ),
)
@click.option(
    "--demo",
    is_flag=True,
    help="Render the seeded fleet-wide snapshot instead of measuring this host.",
)
@click.option(
    "--output",
    "output",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Where to write the HTML (default: the runtime tree).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the snapshot as JSON to stdout.")
def fleet_status_cmd(demo: bool, output: str | None, as_json: bool) -> None:
    snapshot = demo_snapshot() if demo else gather_fleet_snapshot()

    if as_json:
        click.echo(json.dumps(_snapshot_dict(snapshot), indent=2))
        return

    out_path = Path(output).expanduser() if output else _default_output()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_dashboard_html(snapshot), encoding="utf-8")

    flagged = snapshot.flagged_count
    cnl = snapshot.could_not_look_count
    click.echo(f"Wrote fleet dashboard to {out_path}")
    click.echo(
        f"  {snapshot.total_hosts} host(s), {snapshot.total_filesystems} "
        f"filesystem(s), {flagged} flagged, {cnl} could-not-look."
    )


# EOF
