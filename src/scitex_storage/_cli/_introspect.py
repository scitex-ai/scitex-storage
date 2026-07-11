#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``list-python-apis`` — introspect scitex_storage's public Python API."""

from __future__ import annotations

import inspect
import json

import click

from ._compat import spec_command_kwargs


def _api_tree() -> list[dict]:
    """Public members of :mod:`scitex_storage`, per ``__all__``."""
    import scitex_storage

    rows: list[dict] = []
    for name in getattr(scitex_storage, "__all__", ()):
        obj = getattr(scitex_storage, name, None)
        if obj is None:
            continue
        if inspect.isclass(obj):
            kind = "C"
        elif callable(obj):
            kind = "F"
        else:
            kind = "V"
        try:
            sig = str(inspect.signature(obj)) if kind == "F" else ""
        except (TypeError, ValueError):
            sig = ""
        rows.append({"name": name, "type": kind, "signature": sig})
    return rows


@click.command(
    "list-python-apis",
    **spec_command_kwargs(
        summary="List scitex_storage's public Python API (__all__).",
        examples=(
            ("{prog} list-python-apis", "text tree"),
            ("{prog} list-python-apis --json", "JSON"),
        ),
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def list_python_apis(as_json: bool) -> None:
    rows = _api_tree()
    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return
    click.secho(f"scitex_storage public API ({len(rows)} items):", fg="cyan")
    for row in rows:
        click.echo(f"  [{row['type']}] {row['name']}{row['signature']}")


# EOF
