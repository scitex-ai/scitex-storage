#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage purge`` — audited deletion of regenerable paths.

Dry-run by default. With ``--yes`` every path, deleted or refused, gets a
JSON audit record under ``~/.scitex/scitex-storage/runtime/purge-logs/``.
See :mod:`scitex_storage._transfer._purge` for the gates.
"""

from __future__ import annotations

import json

import click

from .._transfer._purge import ELIGIBLE, apply_purge, log_dir, plan_purge
from ._compat import spec_command_kwargs
from ._reclaim_cmd import _run_id


def _gib(n: int) -> str:
    return f"{n / 2**30:.2f} GiB"


@click.command(
    "purge",
    **spec_command_kwargs(
        summary="Delete regenerable PATH(s) (caches, build/test scratch), audited.",
        description=(
            "For data whose recipe is the network or a lockfile: package "
            "caches, merged worktrees' venvs, CI job temp. Each PATH must be "
            "regenerable (`find-recipe` says cache/regenerable) or carry an "
            "operator --reason saying why; a path a live process holds open, "
            "maps, or uses as its cwd is refused (the /proc scan carries a "
            "positive control); mount points and trees containing one are "
            "refused; --min-age-days refuses trees with a recent entry. "
            "Every path gets an audit record written before and after the "
            "delete. Unknown-value data belongs to `reclaim`, `move` or "
            "`archive`, never here. Defaults to a dry-run (--dry-run is accepted "
            "explicitly too); audit records live under "
            "~/.scitex/scitex-storage/runtime/purge-logs/."
        ),
        examples=(
            ("{prog} purge ~/.cache/uv", "preview (uv writes CACHEDIR.TAG)"),
            (
                "{prog} purge /tmp/pytest-of-$USER --min-age-days 3 "
                "--reason 'pytest basetemp' --yes",
                "delete stale pytest temp, audited",
            ),
        ),
    ),
)
@click.argument("paths", nargs=-1, required=True, type=click.Path())
@click.option(
    "--reason",
    default=None,
    help="Why these paths are regenerable. Required for paths find-recipe "
    "cannot classify; stored in every audit record.",
)
@click.option(
    "--min-age-days",
    type=float,
    default=None,
    help="Refuse a path whose newest entry is younger than this.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Only report the plan (the default); wins over --yes.",
)
@click.option("--yes", "-y", "confirmed", is_flag=True, help="Actually delete.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def purge_cmd(paths, reason, min_age_days, dry_run, confirmed, as_json):
    confirmed = confirmed and not dry_run
    import time

    plan = plan_purge(
        list(paths),
        run_id=_run_id(time.time()),
        reason=reason,
        min_age_days=min_age_days,
    )
    records = apply_purge(plan) if confirmed else None

    if as_json:
        click.echo(
            json.dumps(
                {
                    "run_id": plan.run_id,
                    "applied": confirmed,
                    "log_dir": str(log_dir() / plan.run_id) if confirmed else None,
                    "eligible_bytes": plan.eligible_bytes,
                    "entries": records
                    if records is not None
                    else [e.__dict__ for e in plan.entries],
                },
                indent=2,
            )
        )
        return
    verb = "deleted" if confirmed else "would delete"
    for entry in plan.entries:
        mark = "OK " if entry.decision == ELIGIBLE else "NO "
        why = "" if entry.decision == ELIGIBLE else f"  -- {entry.refusal}"
        click.echo(f"{mark} {_gib(entry.size_bytes):>12}  {entry.path}{why}")
    failed = [r for r in records or [] if r["status"] == "failed"]
    click.echo(
        f"{verb} {len(plan.eligible)}/{len(plan.entries)} path(s), "
        f"{_gib(plan.eligible_bytes)}"
        + (f"; {len(failed)} failed" if failed else "")
        + (f"; audit: {log_dir() / plan.run_id}" if confirmed else " (dry-run)")
    )


# EOF
