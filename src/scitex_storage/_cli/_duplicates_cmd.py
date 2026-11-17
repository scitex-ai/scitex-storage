#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage find-duplicates`` — fclones-backed exact-duplicate finder."""

from __future__ import annotations

import json

import click

from .._measure._duplicate_locations import group_duplicates as _group_duplicates
from .._measure._duplicates import find_duplicates as _find_duplicates
from .._measure._duplicates import overlap_bytes as _overlap_bytes
from .._measure._duplicates import size_groups as _size_groups
from .._measure._scan import MissingSystemDependencyError
from .._report import (
    duplicates_to_json_dict,
    format_duplicates_report,
    format_overlap_report,
    format_size_groups_report,
)
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
            (
                "{prog} find-duplicates dump-a dump-b --sizes-only",
                "stage 1: bytes at stake before hashing",
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
@click.option(
    "--group-by",
    type=click.Choice(["parent", "top-level"]),
    default=None,
    help=(
        "Break down each confirmed content group by immediate parent "
        "or top-level folder."
    ),
)
@click.option(
    "--sizes-only",
    is_flag=True,
    help=(
        "Stage 1 only: group files by size (stat-only, reads no contents) "
        "and report the bytes at stake. No `fclones` needed."
    ),
)
def find_duplicates_cmd(
    paths: tuple[str, ...],
    max_depth: int | None,
    as_json: bool,
    sizes_only: bool,
    group_by: str | None,
) -> None:
    if sizes_only and group_by:
        raise click.UsageError(
            "--group-by requires content hashing; omit --sizes-only."
        )
    if sizes_only:
        try:
            by_size = _size_groups(list(paths), max_depth=max_depth)
        except OSError as exc:
            raise click.ClickException(f"Size-only scan failed: {exc}") from exc
        if as_json:
            click.echo(
                json.dumps(
                    {
                        "shared_sizes": len(by_size),
                        "candidate_files": sum(len(p) for p in by_size.values()),
                        "bytes_at_stake": (
                            sum((len(p) - 1) * s for s, p in by_size.items())
                            if by_size
                            else None
                        ),
                        "groups": {
                            str(s): [str(p) for p in ps] for s, ps in by_size.items()
                        },
                    },
                    indent=2,
                )
            )
        else:
            click.echo(format_size_groups_report(by_size))
        return
    try:
        groups = _find_duplicates(list(paths), max_depth=max_depth)
        locations = (
            _group_duplicates(groups, by=group_by, roots=list(paths))
            if group_by
            else None
        )
    except (MissingSystemDependencyError, RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    overlap = _overlap_bytes(groups, list(paths)) if len(paths) >= 2 else {}
    if as_json:
        payload = duplicates_to_json_dict(groups)
        if locations is not None:
            payload.update(group_by=group_by, location_groups=locations)
        if overlap:
            payload["overlap_bytes"] = overlap
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(format_duplicates_report(groups))
        if locations is not None:
            click.echo()
            click.echo(f"Duplicate locations ({group_by}):")
            for item in locations:
                click.echo(f"  Group {item['group_index'] + 1}: {item['relationship']}")
                for location in item["locations"]:
                    click.echo(f"    {location['directory']}:")
                    for path in location["files"]:
                        click.echo(f"      {path}")
        if overlap:
            click.echo()
            click.echo(format_overlap_report(overlap))


# EOF
