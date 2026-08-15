#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage sweep`` / ``sweep-status`` — inode-hog tar-in-place rotation."""

from __future__ import annotations

import json

import click

from .._report import (
    format_sweep_report,
    format_sweep_status_report,
    sweep_plan_to_json_dict,
    sweep_status_to_json_dict,
)
from .._transfer._sweep import apply_sweep, plan_sweep, sweep_status
from ._compat import spec_command_kwargs


@click.command(
    "sweep",
    **spec_command_kwargs(
        summary="Tar an inode-hog directory in place (many files -> one).",
        description=(
            "Scans the immediate children of DIRECTORY (via `scan`) for "
            "ones with at least --threshold-files files whose newest file "
            "is older than --min-age-hours (skips anything that looks still "
            "in use). Defaults to a dry-run listing candidates. --apply "
            "requires an explicit --confirm NAME for every directory to "
            "actually tar+remove — never a blanket 'sweep everything the "
            "plan found'. COMPUTE-NODE-ONLY: --apply refuses to run unless "
            "$SLURM_JOB_ID is set (tar reads file content; submit via "
            "sbatch/srun, never on a login node). "
            "NEEDS FREE SPACE TO FREE SPACE: the tar is written beside the "
            "source, on the SAME filesystem, so sweep temporarily needs room "
            "for an artifact roughly the size of what it is sweeping. It "
            "preflights and refuses rather than filling the disk, but that "
            "makes it UNUSABLE on a filesystem already out of space -- which "
            "is exactly when someone reaches for a cleanup tool. If the "
            "filesystem is full, move data OFF it first (`archive` to a "
            "remote, or `reclaim --archive-root` onto another filesystem). "
            "sweep trades INODES for fewer files using space headroom you "
            "still have; it does not rescue a disk at 100%.",
        ),
        examples=(
            (
                "{prog} sweep /data/gpfs/projects/punim0264/runs --threshold-files 5000",
                "dry-run, list inode-hog children",
            ),
            (
                "{prog} sweep DIR --threshold-files 5000 --apply --confirm old-run-42",
                "sweep exactly one reviewed directory (inside an sbatch job)",
            ),
        ),
    ),
)
@click.argument("directory", type=click.Path())
@click.option(
    "--threshold-files",
    type=int,
    required=True,
    help="Minimum file count to qualify as a candidate (no default -- pick deliberately).",
)
@click.option(
    "--min-age-hours",
    type=float,
    default=24.0,
    show_default=True,
    help="Exclude a candidate if its newest file is younger than this many hours.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Actually tar+remove. Without this flag, sweep only reports the plan.",
)
@click.option(
    "--confirm",
    "confirm_names",
    multiple=True,
    metavar="NAME",
    help="Name of a candidate to actually sweep (repeatable). Required with --apply.",
)
@click.option(
    "--min-remaining-seconds",
    type=float,
    default=300.0,
    show_default=True,
    help="Stop before starting a candidate if less walltime than this remains.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def sweep_cmd(
    directory: str,
    threshold_files: int,
    min_age_hours: float,
    do_apply: bool,
    confirm_names: tuple[str, ...],
    min_remaining_seconds: float,
    as_json: bool,
) -> None:
    if do_apply and not confirm_names:
        raise click.ClickException(
            "--apply requires at least one --confirm NAME (never a blanket apply)"
        )
    plan = plan_sweep(
        directory, threshold_files=threshold_files, min_age_seconds=min_age_hours * 3600
    )
    result = (
        apply_sweep(
            plan, confirm_names=list(confirm_names), min_remaining_seconds=min_remaining_seconds
        )
        if do_apply
        else None
    )
    if as_json:
        click.echo(
            json.dumps(sweep_plan_to_json_dict(plan, applied=do_apply, result=result), indent=2)
        )
    else:
        click.echo(format_sweep_report(plan, applied=do_apply, result=result))


@click.command(
    "sweep-status",
    **spec_command_kwargs(
        summary="List directories under DIRECTORY already swept into a tar.",
        description=(
            "Read-only. A child is 'swept' if a sibling <name>.tar exists "
            "directly in DIRECTORY. Flags the anomalous case where the "
            "original directory of the same name is somehow still present "
            "alongside its tar.",
        ),
        examples=(
            (
                "{prog} sweep-status /data/gpfs/projects/punim0264/runs",
                "list what's already been swept",
            ),
        ),
    ),
)
@click.argument("directory", type=click.Path())
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def sweep_status_cmd(directory: str, as_json: bool) -> None:
    entries = sweep_status(directory)
    if as_json:
        click.echo(json.dumps(sweep_status_to_json_dict(directory, entries), indent=2))
    else:
        click.echo(format_sweep_status_report(directory, entries))


# EOF
