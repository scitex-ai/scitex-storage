#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage scan`` — read-only per-child size + inode inventory."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .._report import format_report, to_json_dict
from .._scan import MissingSystemDependencyError
from .._scan import scan as _scan
from ._compat import spec_command_kwargs

# Where research junk accumulates first on a SciTeX box: the tool's own
# state tree and the projects tree. Used when ``scan`` is given no PATH.
DEFAULT_ROOTS: tuple[str, ...] = ("~/.scitex", "~/proj")


def _resolve_roots(paths: tuple[str, ...]) -> list[Path]:
    """Validate PATHs into scan roots.

    Explicit PATHs fail loud (``ClickException``) when missing / not a
    directory. When no PATH is given, the ``DEFAULT_ROOTS`` are used and any
    that do not exist are skipped with a stderr note (a box legitimately may
    not have ``~/proj``).
    """
    explicit = bool(paths)
    raw = list(paths) if explicit else list(DEFAULT_ROOTS)
    roots: list[Path] = []
    for item in raw:
        p = Path(item).expanduser()
        if not p.exists() or not p.is_dir():
            if explicit:
                raise click.ClickException(
                    f"path does not exist or is not a directory: {p}"
                )
            click.echo(f"(skipping missing default root: {p})", err=True)
            continue
        roots.append(p)
    if not roots:
        raise click.ClickException("no existing directories to scan")
    return roots


@click.command(
    "scan",
    **spec_command_kwargs(
        summary="Inventory the biggest space + inode consumers under PATH(s).",
        description=(
            "For each immediate (top-level) child of every PATH, report the "
            "total bytes and the total file inodes beneath it, sorted so the "
            "worst offenders surface first. With no PATH, scans ~/.scitex and "
            "~/proj. Read-only and stat-only: never follows symlinked "
            "directories (no network-mount storms), never reads file "
            "contents, and never modifies anything.",
        ),
        examples=(
            ("{prog} scan", "inventory ~/.scitex and ~/proj (text)"),
            ("{prog} scan ~/proj --sort files", "rank children by inode count"),
            ("{prog} scan /data --top 40 --json", "top 40 children, JSON"),
            ("{prog} scan /mnt/nfs --max-depth 2", "cap recursion on a slow path"),
        ),
    ),
)
@click.argument("paths", nargs=-1, type=click.Path())
@click.option(
    "--top",
    type=int,
    default=20,
    show_default=True,
    help="Number of top children to report per root.",
)
@click.option(
    "--sort",
    type=click.Choice(["size", "files"]),
    default="size",
    show_default=True,
    help="Rank children by total size or by inode (file) count.",
)
@click.option(
    "--max-depth",
    type=int,
    default=None,
    help="Cap recursion depth per child (login-node safety). Default: unlimited.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def scan_cmd(
    paths: tuple[str, ...],
    top: int,
    sort: str,
    max_depth: int | None,
    as_json: bool,
) -> None:
    roots = _resolve_roots(paths)
    try:
        results = [_scan(p, max_depth=max_depth) for p in roots]
    except MissingSystemDependencyError as exc:
        # Clean, actionable error -- never a raw traceback, and never a
        # silent fallback to a slow pure-Python walk (see _scan.py).
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_json_dict(results, top=top, sort=sort), indent=2))
    else:
        click.echo(format_report(results, top=top, sort=sort))


# EOF
