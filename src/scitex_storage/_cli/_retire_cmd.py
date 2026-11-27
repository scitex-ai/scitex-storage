#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage retire`` -- delete a retained source once its archive re-verifies.

The CLI over :mod:`.._transfer._retire`. Dry-run by default: the plan reads
the manifest and walks the source and siblings, and contacts nothing. ``--yes``
runs the remote content scrub, re-hashes the source, re-proves every sibling,
and only then deletes.

EXIT CODES, in this package's own range so no verdict reuses 1/2:

  0   planned (dry-run, every offline check passed) or retired
  18  refused: a gate failed, nothing was deleted
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs

EXIT_REFUSED = 18


@click.command(
    "retire",
    **spec_command_kwargs(
        summary="Delete a backed-up source after re-verifying its archive.",
        description=(
            "Reads an archive MANIFEST written by `backup` (content-verified, "
            "source retained). With --yes it scrubs the remote copy against the "
            "manifest's content digest, re-hashes the local source to the same "
            "digest, proves each --also directory holds only hard links to source "
            "files, records the audit, deletes the siblings and the source, and "
            "records bytes freed. Nothing is staged. Any failed or unreadable "
            "check refuses with exit 18 and deletes nothing. Defaults to a dry-run.",
        ),
        examples=(
            (
                "{prog} retire ~/.scitex/scitex-storage/runtime/archive-manifests/<name>.json",
                "plan only",
            ),
            (
                "{prog} retire <manifest> --also ~/data-snapshot --yes",
                "verify, then delete source and hard-linked sibling",
            ),
        ),
    ),
)
@click.argument("manifest", type=click.Path())
@click.option(
    "--also",
    "also",
    multiple=True,
    type=click.Path(),
    help="A hard-linked sibling tree to delete too (repeatable). Every file "
    "must be a hard link to a source file.",
)
@click.option("--yes", "-y", "confirmed", is_flag=True, help="Verify and delete.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def retire_cmd(
    manifest: str, also: tuple[str, ...], confirmed: bool, as_json: bool
) -> None:
    from .._transfer._retire import RetireRefusedError, apply_retire, plan_retire

    try:
        plan = plan_retire(manifest, also)
    except (FileNotFoundError, ValueError) as exc:
        raise click.BadParameter(str(exc), param_hint="MANIFEST") from exc

    audit = None
    refused = None
    if confirmed:
        try:
            audit = apply_retire(plan)
        except RetireRefusedError as exc:
            refused = str(exc)
    elif not plan.ready:
        refused = "; ".join(plan.refusals)

    payload = {
        **plan.to_dict(),
        "applied": confirmed,
        "refused": refused,
        "retirement": audit,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(_format(payload))
    if refused:
        raise SystemExit(EXIT_REFUSED)


def _format(p: dict) -> str:
    gib = 1024**3
    lines = [
        "scitex-storage retire",
        f"  manifest:  {p['manifest_path']}",
        f"  archive:   {p['destination']}:{p['remote_path']}",
        f"  source:    {p['source']}  ({p['source_file_count']:,} entries, "
        f"{p['source_bytes'] / gib:.1f} GiB)",
    ]
    for s in p["siblings"]:
        lines.append(f"  --also:    {s['path']}  {s['verdict']}: {s['evidence']}")
    lines.append(f"  expected freed: {p['expected_bytes_freed'] / gib:.1f} GiB")
    if p["refused"]:
        lines.append(f"  REFUSED: {p['refused']}")
    elif not p["applied"]:
        lines.append("  (dry-run -- pass --yes to scrub, re-hash, and delete)")
    else:
        r = p["retirement"]
        lines.append(
            f"  RETIRED: freed {r['bytes_freed'] / gib:.1f} GiB; audit in the manifest and {r['transfer_log']}"
        )
    return "\n".join(lines)


# EOF
