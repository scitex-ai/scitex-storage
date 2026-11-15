#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage relocate`` / ``relocate-restore`` — apply a move plan reversibly."""

from __future__ import annotations

import json
import time

import click

from .._transfer._relocate import (
    RelocateConflict,
    apply_relocate,
    load_plan_file,
    plan_relocate,
    restore_relocate,
)
from ._compat import spec_command_kwargs
from ._reclaim_cmd import _run_id


@click.command(
    "relocate",
    **spec_command_kwargs(
        summary="Apply a YAML move plan to a tree: simulated, merge-safe, reversible.",
        description=(
            "Reads PLAN (YAML: ordered `moves` of {from, to}, optional `drop`, "
            "`junk` {prefixes, suffixes}, directory `links` {path, target}, and "
            "`skip`). Paths are relative to --root. The plan is SIMULATED in order "
            "on a virtual index of the tree, so the dry run shows exactly what --yes "
            "will do, including merges into folders that already exist. An "
            "identical file at the destination is a duplicate; the source copy is "
            "archived. Different content is a CONFLICT: the whole plan is refused "
            "and nothing changes. Junk, drops and duplicates are never deleted. "
            "They move into <archive-root>/<run-id>/ with their relative path "
            "preserved, and `relocate-restore RUN_ID` replays the run backwards. "
            "Links are relative directory symlinks only; file links are refused "
            "because Windows Explorer cannot open them through \\\\wsl.localhost. "
            "Defaults to a dry-run."
        ),
        examples=(
            (
                "{prog} relocate plan.yaml --root ~/proj/docs",
                "preview the plan (dry-run)",
            ),
            ("{prog} relocate plan.yaml --root ~/proj/docs --yes", "apply it"),
            ("{prog} relocate-restore 2026-1116-093000-123456", "undo a run"),
        ),
    ),
)
@click.argument("plan_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--root", "root", type=click.Path(exists=True, file_okay=False), required=True
)
@click.option(
    "--archive-root",
    "archive_root",
    type=click.Path(),
    default=None,
    help="Where junk/drops/duplicates go (default: <root>/.old/relocate).",
)
@click.option("--yes", "-y", "confirmed", is_flag=True, help="Actually apply the plan.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.option("--verbose", "-v", is_flag=True, help="List every operation.")
def relocate_cmd(plan_file, root, archive_root, confirmed, as_json, verbose) -> None:
    try:
        spec = load_plan_file(plan_file)
        plan = plan_relocate(
            spec, root, run_id=_run_id(time.time()), archive_root=archive_root
        )
    except (ValueError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc
    applied = False
    if confirmed and not plan.conflicts:
        try:
            apply_relocate(plan)
        except RelocateConflict as exc:  # pragma: no cover - guarded above
            raise click.ClickException(str(exc)) from exc
        applied = True
    if as_json:
        click.echo(
            json.dumps(
                {
                    "run_id": plan.run_id,
                    "root": plan.root,
                    "archive_root": plan.archive_root,
                    "applied": applied,
                    "conflicts": plan.conflicts,
                    "ops": [o.__dict__ for o in plan.ops],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        click.echo(_format(plan, applied, verbose))
    if plan.conflicts:
        raise SystemExit(1)


def _format(plan, applied: bool, verbose: bool) -> str:
    lines = [f"scitex-storage relocate  (run {plan.run_id})", "=" * 40]
    if verbose or len(plan.ops) <= 40:
        for o in plan.ops:
            if o.kind == "move":
                lines.append(f"  MOVE     {o.src}  ->  {o.dst}")
            elif o.kind == "archive":
                lines.append(f"  ARCHIVE  {o.src}  ({o.reason})")
            else:
                lines.append(f"  LINK     {o.src}  ->  {o.dst}")
    for c in plan.conflicts:
        lines.append(f"  CONFLICT {c}")
    lines.append("")
    lines.append(
        f"  moves={plan.count('move')}  duplicates={plan.count('archive', 'duplicate')}  "
        f"junk={plan.count('archive', 'junk')}  drops={plan.count('archive', 'drop')}  "
        f"links={plan.count('link')}  conflicts={len(plan.conflicts)}"
    )
    if plan.conflicts:
        lines.append("  REFUSED: resolve the conflicts above; nothing was changed.")
    elif not applied:
        lines.append("  (dry-run -- pass --yes/-y to apply)")
    else:
        lines.append(f"  archived items: {plan.archive_root}/{plan.run_id}/")
        lines.append(f"  Undo with:  scitex-storage relocate-restore {plan.run_id}")
    if plan.is_git_worktree:
        lines.append(
            "  note: git work tree -- history follows the files once you commit the result."
        )
    return "\n".join(lines)


@click.command(
    "relocate-restore",
    **spec_command_kwargs(
        summary="Undo a relocate run: replay its operations backwards.",
        description=(
            "Reads the manifest for RUN_ID and reverses every recorded operation in "
            "reverse order: removes the links it made, moves files and folders back, "
            "and returns archived junk/drops/duplicates to where they were. Refuses "
            "to overwrite anything that has since taken a vacated spot."
        ),
        examples=(("{prog} relocate-restore 2026-1116-093000-123456", "undo a run"),),
    ),
)
@click.argument("run_id")
def relocate_restore_cmd(run_id: str) -> None:
    try:
        m = restore_relocate(run_id)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"restored run {run_id}: {len(m.ops)} operation(s) reversed under {m.root}"
    )


# EOF
