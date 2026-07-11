#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage CLI — ``scitex-storage scan [PATH ...]`` (MVP: read-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .. import __version__
from .._duplicates import find_duplicates as _find_duplicates
from .._report import (
    duplicates_to_json_dict,
    format_duplicates_report,
    format_report,
    to_json_dict,
)
from .._scan import MissingSystemDependencyError
from .._scan import scan as _scan
from ._compat import spec_command_kwargs, spec_group_kwargs
from ._introspect import list_python_apis
from ._mcp_commands import mcp

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

# Where research junk accumulates first on a SciTeX box: the tool's own
# state tree and the projects tree. Used when ``scan`` is given no PATH.
DEFAULT_ROOTS: tuple[str, ...] = ("~/.scitex", "~/proj")


def _print_command_help(cmd, prefix: str, parent_ctx) -> None:
    click.echo(f"\n{'=' * 50}")
    click.echo(prefix)
    click.echo("=" * 50)
    sub_ctx = click.Context(cmd, info_name=prefix.split()[-1], parent=parent_ctx)
    click.echo(cmd.get_help(sub_ctx))
    if isinstance(cmd, click.Group):
        for sub_name, sub_cmd in sorted(cmd.commands.items()):
            _print_command_help(sub_cmd, f"{prefix} {sub_name}", sub_ctx)


@click.group(
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
    **spec_group_kwargs(
        summary="Research-data storage triage (MVP: read-only discovery).",
        description=(
            "Discovery layer of a planned storage-tiering tool (local SSD "
            "-> NAS SSD -> NAS HDD -> offline). `scan` is a read-only, "
            "stat-only directory walk that reports the biggest space and "
            "inode (file-count) consumers per top-level child of a root -- "
            "no file contents are ever read. `find-duplicates` is a "
            "separate, explicitly opt-in verb that DOES read file contents "
            "(to hash them) to report exact-duplicate groups. Nothing "
            "moves, copies, or deletes anything in this release.",
        ),
        config_resolution=(
            "scitex-storage has no configurable state in this MVP — every "
            "command takes its options on the command line. Runtime state, "
            "if any is ever added, lives under ~/.scitex/scitex-storage/"
            "runtime/; nothing reads or writes it yet.",
        ),
        version_of="scitex-storage",
        command_categories=(
            ("Storage", ("scan", "find-duplicates")),
            ("Introspection", ("list-python-apis", "mcp")),
        ),
    ),
)
@click.version_option(
    __version__,
    "-V",
    "--version",
    prog_name="scitex-storage",
    message="%(prog)s %(version)s",
)
@click.option("--help-recursive", is_flag=True, help="Show help for all commands.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit structured JSON output (propagates to subcommands that honour it).",
)
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json

    if help_recursive:
        click.echo(f"scitex-storage {__version__}")
        click.echo(main.get_help(ctx))
        for name, cmd in sorted(main.commands.items()):
            _print_command_help(cmd, f"scitex-storage {name}", ctx)
        ctx.exit(0)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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


@main.command(
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


@main.command(
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


main.add_command(list_python_apis)
main.add_command(mcp)

# §1a: install-shell-completion + print-shell-completion (canonical leaves).
try:
    from scitex_dev._cli._completion import attach_shell_completion

    attach_shell_completion(main, prog_name="scitex-storage")
except ImportError:
    pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# EOF
