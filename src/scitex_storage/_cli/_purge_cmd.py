#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage purge`` -- audited deletion of a tree with no copy to keep.

The CLI over :mod:`.._transfer._purge`. Dry-run by default: the plan walks the
tree and inspects mounts and processes, and deletes nothing. ``--yes`` plans
again, writes the audit record, deletes, and records bytes freed.

EXIT CODES, matching ``retire``:

  0   planned (dry-run, every guard passed) or purged
  18  refused: a guard failed, nothing was deleted
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs

EXIT_REFUSED = 18


@click.command(
    "purge",
    **spec_command_kwargs(
        summary="Delete a disposable local tree, guarded and audited.",
        description=(
            "For data that needs no copy (a leftover from a finished migration, "
            "a stale cache). Refuses `/`, top-level paths, mount points, symlinks "
            "and the home directory; refuses when any mount (including a bind "
            "mount from inside PATH, in any mount namespace) or any process "
            "(cwd, root, exe, open file, mapping) uses PATH, or when a process "
            "cannot be inspected. --confirm must repeat PATH exactly and --reason "
            "is required. With --yes it plans again, writes an fsynced audit "
            "record under ~/.scitex/scitex-storage/runtime/purge-logs/ before the "
            "first unlink, deletes, and records free space before and after. "
            "Nothing is staged and nothing can be restored. Defaults to a dry-run.",
        ),
        examples=(
            (
                "{prog} purge /scratch/old-root --confirm /scratch/old-root --reason 'superseded by /scratch/root'",
                "plan only",
            ),
            (
                "sudo {prog} purge /scratch/old-root --confirm /scratch/old-root --reason '...' --yes",
                "plan again, audit, delete",
            ),
        ),
    ),
)
@click.argument("path", type=click.Path())
@click.option(
    "--confirm", required=True, help="Repeat PATH exactly (guards against typos)."
)
@click.option(
    "--reason", required=True, help="Why this tree is disposable (kept in the audit)."
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only (explicit spelling of the default; wins over --yes).",
)
@click.option("--yes", "-y", "confirmed", is_flag=True, help="Actually delete.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def purge_cmd(
    path: str,
    confirm: str,
    reason: str,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    from .._transfer._purge import PurgeRefusedError, apply_purge, plan_purge

    plan = plan_purge(path, reason=reason, confirm=confirm)
    audit = None
    refused = None
    if confirmed and not dry_run:
        # apply_purge plans again and audits a refusal as well as a purge.
        try:
            audit = apply_purge(plan)
        except PurgeRefusedError as exc:
            refused = str(exc)
    elif not plan.ready:
        refused = "; ".join(plan.refusals)

    payload = {
        **plan.to_dict(),
        "applied": audit is not None,
        "refused": refused,
        "audit": audit,
    }
    click.echo(json.dumps(payload, indent=2) if as_json else _format(payload))
    if refused:
        raise SystemExit(EXIT_REFUSED)


def _format(p: dict) -> str:
    gib = 1024**3
    lines = [
        "scitex-storage purge",
        f"  path:      {p['path']}",
        f"  reason:    {p['reason']}",
        f"  entries:   {p['entries']:,}  ({p['allocated_bytes'] / gib:.1f} GiB allocated, "
        f"{p['apparent_bytes'] / gib:.1f} GiB apparent)",
        f"  expected freed: {p['expected_bytes_freed'] / gib:.1f} GiB"
        + (
            f"  ({p['hard_linked_outside']:,} files hard-linked from outside stay)"
            if p["hard_linked_outside"]
            else ""
        ),
    ]
    if p["refused"]:
        lines.append(f"  REFUSED: {p['refused']}")
    elif not p["applied"]:
        lines.append("  (dry-run -- pass --yes to re-check, audit, and delete)")
    else:
        a = p["audit"]
        lines.append(
            f"  PURGED: freed {a['bytes_freed'] / gib:.1f} GiB in {a['seconds']}s; audit {a['audit_log']}"
        )
    return "\n".join(lines)


# EOF
