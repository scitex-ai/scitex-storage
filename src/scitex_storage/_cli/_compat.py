#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarded imports of scitex-dev's CLI-standardization helpers (+ fallback).

scitex-dev's ``scitex_dev.ecosystem`` facade ships ``CliHelp`` / ``Example``
/ ``SpecCommand`` on recent releases, but scitex-storage pins only
``scitex-dev>=0.21.0`` in ``[dev]`` — an environment can legitimately have
an older (or, on a fresh checkout with no dev extra installed at all, no)
scitex-dev present. Importing ``scitex_dev.ecosystem`` directly at module
scope would make ``import scitex_storage`` itself fail whenever that
version mismatch (or absence) occurs — this exact class of bug was found
and fixed in scitex-todo's ``_cli/_compat.py`` this session, so it is not
repeated here.

* **scitex-dev with the spec-help helpers installed** — re-export the real
  ``CliHelp`` / ``Example`` / ``SpecCommand`` (single source of truth).
* **Older/missing scitex-dev** — degrade to plain click ``help=`` /
  ``short_help=`` text. The CLI surface (flags, output) is identical either
  way; only the rendered ``--help`` formatting differs.

Call sites (``_cli/__init__.py``) never branch on availability themselves.
"""

from __future__ import annotations

import click

try:
    from scitex_dev.ecosystem import CliHelp, Example, SpecCommand, SpecGroup

    HAS_SPEC_HELP = True
except ImportError:  # scitex-dev absent, or a release without these helpers
    CliHelp = Example = SpecCommand = SpecGroup = None  # type: ignore[assignment]
    HAS_SPEC_HELP = False

__all__ = ["HAS_SPEC_HELP", "spec_command_kwargs", "spec_group_kwargs"]


def _render_fallback_help(
    summary: str,
    description: tuple[str, ...],
    examples: tuple[tuple[str, str], ...],
    prog: str = "scitex-storage",
) -> str:
    """Plain help body used when scitex-dev's spec-help helpers are absent."""
    blocks: list[str] = [summary, *description]
    if examples:
        lines = ["\b", "Examples:"]
        lines.extend(
            f"  $ {cmd.replace('{prog}', prog)}" + (f"  {note}" if note else "")
            for cmd, note in examples
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def spec_command_kwargs(
    *,
    summary: str,
    description: str | tuple[str, ...] = (),
    examples: tuple[tuple[str, str], ...] = (),
) -> dict:
    """``click.command`` kwargs for a spec-built leaf command.

    ``examples`` is a tuple of ``(cmd, note)`` pairs; ``cmd`` uses the
    ``{prog}`` placeholder per the CliHelp contract.
    """
    if isinstance(description, str):
        description = (description,)
    if HAS_SPEC_HELP:
        return {
            "cls": SpecCommand,
            "help_spec": CliHelp(
                summary=summary,
                description=description,
                examples=tuple(Example(cmd, note) for cmd, note in examples),
            ),
        }
    return {
        "help": _render_fallback_help(summary, tuple(description), tuple(examples)),
        "short_help": summary,
    }


def spec_group_kwargs(
    *,
    summary: str,
    description: str | tuple[str, ...] = (),
    config_resolution: tuple[str, ...] = (),
    version_of: str | None = None,
    command_categories: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> dict:
    """``click.group`` kwargs for the spec-built root group."""
    if isinstance(description, str):
        description = (description,)
    if HAS_SPEC_HELP:
        return {
            "cls": SpecGroup,
            "help_spec": CliHelp(
                summary=summary,
                description=description,
                config_resolution=config_resolution,
                version_of=version_of,
            ),
            "command_categories": command_categories,
        }
    kwargs: dict = {
        "help": _render_fallback_help(
            summary, tuple(description), (), prog="scitex-storage"
        )
        + (
            "\n\nConfig resolution:\n" + "\n".join(f"  {r}" for r in config_resolution)
            if config_resolution
            else ""
        ),
        "short_help": summary,
    }
    return kwargs


# EOF
