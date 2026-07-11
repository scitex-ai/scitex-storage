#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage CLI — ``scitex-storage scan <path>`` (MVP: read-only)."""

from __future__ import annotations

import json
import sys

import click

from .. import __version__
from .._report import format_text_report, to_json_dict
from .._scan import scan as _scan
from ._compat import spec_command_kwargs, spec_group_kwargs
from ._introspect import list_python_apis
from ._mcp_commands import mcp

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


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
        summary="Research-data storage triage (MVP: read-only scan).",
        description=(
            "Discovery layer of a planned storage-tiering tool (local SSD "
            "-> NAS SSD -> NAS HDD -> offline). This release ships only "
            "`scan`: a read-only directory-tree walk that scores files by "
            "size x days-since-last-access and reports the biggest, "
            "stalest candidates plus (size+hash) duplicate groups. Nothing "
            "moves, copies, or deletes anything.",
        ),
        config_resolution=(
            "scitex-storage has no configurable state in this MVP — `scan` "
            "takes every option on the command line. A future config.yaml "
            "under ~/.scitex/storage/ is on the roadmap; nothing reads it "
            "yet.",
        ),
        version_of="scitex-storage",
        command_categories=(
            ("Storage", ("scan",)),
            ("Introspection", ("list-python-apis", "mcp")),
        ),
    ),
)
@click.version_option(
    __version__, "-V", "--version", prog_name="scitex-storage",
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


@main.command(
    "scan",
    **spec_command_kwargs(
        summary="Scan a directory tree and report size x staleness candidates.",
        description=(
            "Walks PATH (skipping .git/node_modules/.venv/build/dist/... — "
            "regenerable dirs), scores each file by "
            "size_bytes * days_since_last_access, and prints the biggest, "
            "stalest files first. Read-only: never moves, deletes, or "
            "modifies anything.",
        ),
        examples=(
            ("{prog} scan ~/projects", "text report, top 20"),
            ("{prog} scan ~/projects --top 50 --json", "JSON, top 50"),
            ("{prog} scan ~/projects --no-dedupe", "skip the hash-based duplicate pass"),
        ),
    ),
)
@click.argument("path", type=click.Path(exists=True, file_okay=False))
@click.option("--top", type=int, default=20, show_default=True, help="Top-N candidates to report.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON instead of text.")
@click.option(
    "--dedupe/--no-dedupe",
    default=True,
    show_default=True,
    help="Run the (size+hash) duplicate-file pass.",
)
def scan_cmd(path: str, top: int, as_json: bool, dedupe: bool) -> None:
    result = _scan(path, top=top, dedupe=dedupe)
    if as_json:
        click.echo(json.dumps(to_json_dict(result, top=top), indent=2))
    else:
        click.echo(format_text_report(result, top=top))


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
