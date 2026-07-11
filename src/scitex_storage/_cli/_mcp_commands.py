#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``mcp`` command group — structural placeholder (no MCP server yet).

scitex-storage ships no ``fastmcp`` dependency and no MCP tool surface in
this MVP; the ``mcp list-tools`` leaf still exists (per the ecosystem's
CLI-standardization contract, audit-cli §1a) so every scitex-* CLI has the
same discoverable shape, but it reports "no tools" instead of pretending
one exists.
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs, spec_group_kwargs

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
    **spec_group_kwargs(
        summary="MCP (Model Context Protocol) server commands.",
        description=(
            "scitex-storage has no MCP server in this MVP — these commands "
            "exist for ecosystem-wide CLI discoverability and report zero "
            "tools rather than pretending a server exists.",
        ),
    ),
)
@click.pass_context
def mcp(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@mcp.command(
    "list-tools",
    **spec_command_kwargs(
        summary="List available MCP tools.",
        examples=(
            ("{prog} mcp list-tools", "text (currently: 0 tools)"),
            ("{prog} mcp list-tools --json", "JSON"),
        ),
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def mcp_list_tools(as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps({"tools": []}, indent=2))
        return
    click.echo(
        "scitex-storage has no MCP server yet (0 tools). "
        "See the project roadmap for planned MCP support."
    )


# EOF
