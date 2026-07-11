#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage images prune`` — versioned-file directory rotation."""

from __future__ import annotations

import json

import click

from .._images import apply_prune, plan_prune
from .._report import format_prune_report, prune_plan_to_json_dict
from ._compat import spec_command_kwargs, spec_group_kwargs


@click.group(
    "images",
    **spec_group_kwargs(
        summary="Versioned-image (SIF and friends) directory rotation.",
    ),
)
def images_group() -> None:
    pass


@images_group.command(
    "prune",
    **spec_command_kwargs(
        summary="Rotate a directory of versioned files, keeping the newest N.",
        description=(
            "Scans DIRECTORY (non-recursive) for files matching --pattern "
            "and plans removal of everything past the newest --keep, "
            "EXCEPT any file a symlink directly in DIRECTORY currently "
            "resolves to — those survive regardless of --keep. With "
            "--apply, a second guard also skips (loudly, never raises) any "
            "candidate a running process still has open. Defaults to a "
            "dry-run: prints the plan and reclaimable bytes without "
            "touching the filesystem.",
        ),
        examples=(
            (
                "{prog} images prune ~/.scitex/agent-container/containers/sac-base",
                "dry-run, default --keep 5",
            ),
            (
                "{prog} images prune DIR --keep 3 --apply",
                "actually delete down to 3 (referenced files still excluded)",
            ),
            ("{prog} images prune DIR --pattern '*.tar' --json", "non-SIF pattern, JSON"),
        ),
    ),
)
@click.argument("directory", type=click.Path())
@click.option(
    "--keep",
    type=int,
    default=5,
    show_default=True,
    help="Target number retained (referenced files are kept on top of this).",
)
@click.option(
    "--pattern",
    default="*.sif",
    show_default=True,
    help="Glob matched against candidate filenames.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Actually delete. Without this flag, prune only reports the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def images_prune_cmd(
    directory: str, keep: int, pattern: str, do_apply: bool, as_json: bool
) -> None:
    plan = plan_prune(directory, keep=keep, pattern=pattern)
    apply_result = apply_prune(plan) if do_apply else None
    if as_json:
        click.echo(
            json.dumps(
                prune_plan_to_json_dict(plan, applied=do_apply, apply_result=apply_result),
                indent=2,
            )
        )
    else:
        click.echo(format_prune_report(plan, applied=do_apply, apply_result=apply_result))


# EOF
