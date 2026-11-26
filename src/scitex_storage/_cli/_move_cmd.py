#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage move`` -- a verified local move with a 7-day rollback copy.

The CLI over :mod:`.._transfer._move`, which until now only the web host
could reach. A person moving a directory between local filesystems (a
database data directory off a filling root disk, a project onto a bigger
volume) had no verb for it and fell back to ``cp -a``/``mv`` -- no content
check, no receipt, no rollback.

Dry-run by default: the plan walks SOURCE for size and entry count and writes
nothing. ``--yes`` copies with rsync into a private temporary under the
destination's parent, hashes the copy AND the source independently, publishes
the copy with a no-replace rename, then stages the source next to itself
(``.stx-restorable``) for 7 days instead of deleting it. The receipt is kept
under ``~/.scitex/scitex-storage/runtime/move-manifests/``.

The caller is responsible for quiescing SOURCE (stop the service that writes
it); a source that changes during the move fails verification and nothing is
published.

EXIT CODES, in this package's own range so no verdict reuses 1/2:

  0   planned (dry-run) or moved and verified
  19  refused or failed: SOURCE is untouched, or a receipt names what to recover
"""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from ._compat import spec_command_kwargs

EXIT_MOVE_FAILED = 19


@click.command(
    "move",
    **spec_command_kwargs(
        summary="Move a local directory or file, content-verified, keeping a rollback copy.",
        description=(
            "Copies SOURCE to DESTINATION (which must not exist; its parent must) "
            "with rsync, verifies the copy and the source by content digest, "
            "publishes the copy atomically, and stages SOURCE beside itself for "
            "7 days (restorable) instead of deleting it. Writes a JSON receipt. "
            "Stop anything writing SOURCE first: a source that changes fails "
            "verification and nothing is published. Defaults to a dry-run "
            "(--dry-run is accepted explicitly too)."
        ),
        examples=(
            ("{prog} move ~/old/pgdata /scratch/me/pg/data", "plan only"),
            (
                "{prog} move ~/old/pgdata /scratch/me/pg/data --yes --timeout 600",
                "copy, verify, publish, stage the original",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path())
@click.argument("destination", type=click.Path())
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only (explicit spelling of the default; wins over --yes).",
)
@click.option("--yes", "-y", "confirmed", is_flag=True, help="Actually move.")
@click.option(
    "--timeout",
    "timeout_s",
    type=click.FloatRange(min=0, min_open=True),
    default=120.0,
    show_default=True,
    help="Give up (and publish nothing) after this many seconds.",
)
@click.option(
    "--max-entries",
    type=click.IntRange(min=1),
    default=100_000,
    show_default=True,
    help="Refuse to plan a tree with more files and directories than this.",
)
@click.option(
    "--state-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Where receipts go (default: ~/.scitex/scitex-storage/runtime/move-manifests).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def move_cmd(
    source: str,
    destination: str,
    dry_run: bool,
    confirmed: bool,
    timeout_s: float,
    max_entries: int,
    state_dir: str | None,
    as_json: bool,
) -> None:
    from .._transfer._move import MoveRecoveryRequired, apply_move, plan_move

    try:
        plan = plan_move(
            source, destination, state_dir=state_dir, max_entries=max_entries
        )
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        _emit({"applied": False, "refused": f"{type(exc).__name__}: {exc}"}, as_json)
        raise SystemExit(EXIT_MOVE_FAILED) from exc

    payload = {
        "source": str(plan.source),
        "destination": str(plan.destination),
        "size_bytes": plan.size_bytes,
        "entry_count": plan.entry_count,
        "transaction_id": plan.transaction_id,
        "manifest_path": str(plan.manifest_path),
        "applied": False,
        "refused": None,
        "receipt": None,
    }
    if confirmed and not dry_run:
        try:
            receipt = apply_move(plan, timeout_s=timeout_s)
        except MoveRecoveryRequired as exc:
            payload["refused"] = str(exc)
            payload["receipt"] = asdict(exc.manifest)
            _emit(payload, as_json)
            raise SystemExit(EXIT_MOVE_FAILED) from exc
        except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
            payload["refused"] = f"{type(exc).__name__}: {exc}"
            _emit(payload, as_json)
            raise SystemExit(EXIT_MOVE_FAILED) from exc
        payload["applied"] = True
        payload["receipt"] = asdict(receipt)
    _emit(payload, as_json)


def _emit(p: dict, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(p, indent=2))
        return
    lines = ["scitex-storage move"]
    if "source" in p:
        lines += [
            f"  source:      {p['source']}",
            f"  destination: {p['destination']}",
            f"  size:        {p['size_bytes']:,} B in {p['entry_count']:,} entries",
            f"  receipt:     {p['manifest_path']}",
        ]
    if p["refused"]:
        lines.append(f"  REFUSED: {p['refused']}")
        r = p.get("receipt")
        if r:
            lines.append(f"  phase at stop: {r['phase']} (see the receipt to recover)")
    elif not p["applied"]:
        lines.append("  (dry-run -- pass --yes to copy, verify, publish and stage)")
    else:
        r = p["receipt"]
        lines += [
            f"  MOVED: verified by {r['verification_method']} digest {r['content_digest'][:16]}...",
            f"  original staged for 7 days at: {r['staged_path']}",
        ]
    click.echo("\n".join(lines))


# EOF
