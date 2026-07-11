#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage find-duplicates`` — fclones-backed exact-duplicate finder."""

from __future__ import annotations

import json

import click

from .._duplicates import find_duplicates as _find_duplicates
from .._report import duplicates_to_json_dict, format_duplicates_report
from .._scan import MissingSystemDependencyError
from ._compat import spec_command_kwargs


@click.command(
    "find-duplicates",
    **spec_command_kwargs(
        summary="Find exact-duplicate files under PATH(s) (reads file contents).",
        description=(
            "Groups files with byte-identical content under one or more "
            "PATHs. Unlike `scan`, this READS FILE CONTENTS to hash them -- "
            "it is not stat-only, and is not safe to run unbounded against "
            "a nearly-full disk or a slow network mount without "
            "--max-depth. Nothing is moved, linked, or deleted; this only "
            "reports.",
        ),
        examples=(
            ("{prog} find-duplicates ~/projects", "text report"),
            ("{prog} find-duplicates ~/projects --json", "JSON output"),
            (
                "{prog} find-duplicates /mnt/nfs --max-depth 3",
                "cap recursion on a slow path",
            ),
        ),
    ),
)
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--max-depth",
    type=int,
    default=None,
    help="Cap recursion depth (login-node / network-path safety). Default: unlimited.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def find_duplicates_cmd(
    paths: tuple[str, ...], max_depth: int | None, as_json: bool
) -> None:
    try:
        groups = _find_duplicates(list(paths), max_depth=max_depth)
    except MissingSystemDependencyError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(duplicates_to_json_dict(groups), indent=2))
    else:
        click.echo(format_duplicates_report(groups))


# EOF
