#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage reclaim`` / ``reclaim-restore`` — reversible move-aside.

`reclaim` moves paths into an archive directory instead of deleting them, so
a cleanup decision can be rough: a wrong call costs a `reclaim-restore`, not
data. Dry-run is the default (it only ever moves user data, so the same
"never mutate without --yes" discipline as `archive`/`sweep` applies), and
the fraction of runs later restored is reported as the honest accuracy
metric for whatever chose the paths.
"""

from __future__ import annotations

import json

import click

from .._reclaim import (
    apply_reclaim,
    list_manifests,
    plan_reclaim,
    restore_rate,
    restore_reclaim,
)
from ._compat import spec_command_kwargs


def _run_id(now: float) -> str:
    """A filesystem-safe, sortable, UNIQUE run id from a POSIX timestamp.

    Microsecond suffix included on purpose: a second-granularity id collides
    when two reclaim runs happen in the same second, which would overwrite
    the first run's manifest and move both into the same ``.old/<id>/`` — a
    real data-safety bug, caught by the restore-rate test doing exactly two
    runs back to back. Passed the timestamp rather than reading the clock so
    the value is explicit at the call site and the helper stays testable.
    """
    import time

    micros = int(round((now - int(now)) * 1_000_000))
    return time.strftime("%Y-%m%d-%H%M%S", time.localtime(now)) + f"-{micros:06d}"


@click.command(
    "reclaim",
    **spec_command_kwargs(
        summary="Move PATH(s) aside into a reversible archive instead of deleting.",
        description=(
            "Relocates each PATH into an archive directory and records where "
            "it came from, so the move can be undone with `reclaim-restore`. "
            "This makes a disposal decision safe to be ROUGH -- a wrong call "
            "costs a move back, not lost data -- which is what lets a "
            "cleanup ship before its classifier is perfect. By default the "
            "archive is an adjacent `.old/<timestamp>/` beside each source "
            "(same filesystem, so the move is an instant atomic rename); this "
            "tidies a tree but does NOT free the source filesystem's inodes "
            "or space -- the files are merely relocated. Pass --archive-root "
            "pointing at a DIFFERENT filesystem to actually reclaim "
            "inodes/space (the files leave the full filesystem; the move is "
            "then a verified copy, not atomic). Deleting archived data is a "
            "separate, later step -- this verb never unlinks anything. "
            "Defaults to a dry-run."
        ),
        examples=(
            ("{prog} reclaim ./node_modules", "preview moving one tree aside (dry-run)"),
            ("{prog} reclaim ./build ./dist --yes", "actually move two trees to .old/"),
            (
                "{prog} reclaim ./huge --archive-root /scratch/archive --yes",
                "move to another filesystem (frees inodes here)",
            ),
            ("{prog} reclaim --status", "show reclaim runs + restore rate"),
        ),
    ),
)
@click.argument("paths", nargs=-1, type=click.Path())
@click.option(
    "--archive-root",
    "archive_root",
    type=click.Path(),
    default=None,
    help="Archive into this dir instead of an adjacent .old/ (use a different "
    "filesystem to actually free inodes/space here).",
)
@click.option(
    "--status",
    "show_status",
    is_flag=True,
    help="Show recorded reclaim runs and the restore rate, then exit.",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Actually move. Without this flag, reclaim only reports the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def reclaim_cmd(
    paths: tuple[str, ...],
    archive_root: str | None,
    show_status: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    if show_status:
        _emit_status(as_json)
        return
    if not paths:
        raise click.ClickException("give at least one PATH to reclaim (or --status).")

    import time

    run_id = _run_id(time.time())
    plan = plan_reclaim(list(paths), run_id=run_id, archive_root=archive_root)
    manifest = apply_reclaim(plan) if confirmed else None

    if as_json:
        payload = {
            "run_id": run_id,
            "archive_root": archive_root,
            "applied": confirmed,
            "total_size_bytes": plan.total_size,
            "total_files": plan.total_files,
            "entries": [
                {"original": e.original, "archived": e.archived, "file_count": e.file_count}
                for e in plan.entries
            ],
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(_format_plan(plan, applied=confirmed))
    del manifest


def _format_plan(plan, applied: bool) -> str:
    lines = [f"scitex-storage reclaim  (run {plan.run_id})", "=" * 40]
    verb = "MOVED" if applied else "WOULD MOVE"
    lines.append(f"  {verb}:")
    for e in plan.entries:
        lines.append(f"    {e.file_count:>8,} files  {e.original}")
        lines.append(f"             -> {e.archived}")
    lines.append("")
    lines.append(f"  {plan.total_files:,} files across {len(plan.entries)} path(s)")
    if not applied:
        lines.append("  (dry-run -- pass --yes/-y to actually move)")
    else:
        lines.append(f"  Undo with:  scitex-storage reclaim-restore {plan.run_id}")
    return "\n".join(lines)


def _emit_status(as_json: bool) -> None:
    manifests = list_manifests()
    rate = restore_rate()
    if as_json:
        click.echo(
            json.dumps(
                {
                    "runs": len(manifests),
                    "restore_rate": rate,
                    "manifests": [
                        {
                            "run_id": m.run_id,
                            "reclaimed_at": m.reclaimed_at,
                            "restored": m.restored,
                            "paths": len(m.entries),
                        }
                        for m in manifests
                    ],
                },
                indent=2,
            )
        )
        return
    lines = ["scitex-storage reclaim --status", "=" * 31]
    if not manifests:
        lines.append("  (nothing reclaimed yet)")
    else:
        for m in manifests:
            mark = "RESTORED" if m.restored else "archived"
            lines.append(f"  {m.run_id}  {mark:>8}  {len(m.entries)} path(s)")
        # restore_rate is the accuracy metric; report "no data" not 0 when
        # there is no denominator (there always is here, but keep the guard).
        rate_txt = "n/a" if rate is None else f"{rate * 100:.0f}%"
        lines.append("")
        lines.append(f"  restore rate: {rate_txt}  (fraction of runs pulled back out)")
    click.echo("\n".join(lines))


@click.command(
    "reclaim-restore",
    **spec_command_kwargs(
        summary="Undo a reclaim run: move its archived paths back where they were.",
        description=(
            "Reads the manifest for RUN_ID and moves every archived path back "
            "to its original location. Refuses to overwrite anything that has "
            "since occupied an original spot, and fails loud if an archived "
            "copy is missing -- a restore never clobbers or silently skips. "
            "This reversal is what the whole reclaim design turns on, and "
            "running it is what feeds the restore-rate accuracy metric."
        ),
        examples=(
            ("{prog} reclaim-restore 2026-0717-154500", "put a run's paths back"),
            ("{prog} reclaim --status", "find run ids + which were restored"),
        ),
    ),
)
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def reclaim_restore_cmd(run_id: str, as_json: bool) -> None:
    try:
        manifest = restore_reclaim(run_id)
    except (FileNotFoundError, FileExistsError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(
            json.dumps(
                {
                    "run_id": manifest.run_id,
                    "restored": True,
                    "paths": [e.original for e in manifest.entries],
                },
                indent=2,
            )
        )
    else:
        click.echo(f"restored {len(manifest.entries)} path(s) from run {run_id}:")
        for e in manifest.entries:
            click.echo(f"  -> {e.original}")


# EOF
