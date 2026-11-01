#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage sweep-restorable`` — delete expired restorable staging.

Staged trees (verified moves whose source was moved aside instead of
deleted) become eligible for deletion once their retention expires
(default 7 days at stage time). This verb lists the expired ones by
default and deletes them only with ``--yes`` -- never a blanket delete,
and never anything the guards refuse (outside a ``.stx-restorable``
directory, or a tree whose marker no longer matches).
"""

from __future__ import annotations

import json

import click

from .._transfer._retention import sweep_restorable
from ._compat import spec_command_kwargs


@click.command(
    "sweep-restorable",
    **spec_command_kwargs(
        summary="Delete restorable-staged trees whose retention expired.",
        description=(
            "Lists staged trees (verified moves kept restorable instead of "
            "deleted) whose 7-day retention has expired. Defaults to a "
            "dry-run listing; --yes deletes exactly the expired, guarded "
            "trees. Entries outside a `.stx-restorable` directory or whose "
            "marker or content checksum no longer matches are refused. "
            "Each pass is bounded; larger trees remain retained until an "
            "explicitly sufficient budget is supplied. No periodic job is enabled."
        ),
        examples=(
            ("{prog} sweep-restorable", "dry-run, list expired staging"),
            (
                "{prog} sweep-restorable --yes",
                "delete exactly the expired staged trees",
            ),
        ),
    ),
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="List expired staging without deleting (the default behavior too).",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Actually delete the expired staged trees.",
)
@click.option("--max-items", type=click.IntRange(min=1), default=32, show_default=True)
@click.option(
    "--max-bytes", type=click.IntRange(min=1), default=1024**3, show_default=True
)
@click.option(
    "--max-entries", type=click.IntRange(min=1), default=4096, show_default=True
)
@click.option(
    "--max-seconds",
    type=click.FloatRange(min=0, min_open=True),
    default=30.0,
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def sweep_restorable_cmd(
    dry_run: bool,
    assume_yes: bool,
    max_items: int,
    max_bytes: int,
    max_entries: int,
    max_seconds: float,
    as_json: bool,
) -> None:
    # --yes is the only thing that deletes: without it this is a dry-run
    # listing, whether --dry-run was spelled out or not.
    do_apply = assume_yes and not dry_run
    report = sweep_restorable(
        dry_run=not do_apply,
        max_items=max_items,
        max_bytes=max_bytes,
        max_entries=max_entries,
        max_seconds=max_seconds,
    )
    if as_json:
        click.echo(
            json.dumps(
                {
                    "removed": report.removed,
                    "retained": report.retained,
                    "pruned": report.pruned,
                    "refused": report.refused,
                    "errors": report.errors,
                },
                indent=2,
            )
        )
        return
    if do_apply:
        for path in report.removed:
            click.echo(f"removed {path}")
    elif report.removed:
        for path in report.removed:
            click.echo(f"expired (dry-run) {path}")
    else:
        click.echo("no expired staging.")
    for path in report.retained:
        click.echo(f"retained (not expired or outside this pass budget) {path}")
    for path in report.pruned:
        click.echo(f"pruned missing record {path}")
    for path in report.refused:
        click.echo(f"REFUSED (not provably staged) {path}")
    for path, error in report.errors.items():
        click.echo(f"error {path}: {error}")


# EOF
