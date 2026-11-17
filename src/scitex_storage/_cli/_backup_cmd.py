#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage backup`` — copy-verified second copy, source retained.

The Backup tab's promise as a command: push SOURCE to --to, verify the
destination independently, write the manifest marked ``retained_source``,
and LEAVE THE SOURCE IN PLACE. Same transport, same gates, same
manifest honesty as ``archive`` -- the ONLY difference is the last line
(no ``shutil.rmtree``). Verification is NOT relaxed for backups: an
unverified copy is not a backup, it is a hope, so a failed read-back
raises exactly as it does for archive.

Dry-run by default; --yes applies. No delete exists anywhere in this verb.
"""

from __future__ import annotations

import json

import click

from .._transfer._archive import apply_archive, plan_archive
from .._transfer._archive_transport import (
    DESTINATIONS,
    RETIRED_DESTINATIONS,
    probe_transport,
    resolve_destination,
)
from ._archive_cmd import _require_rsync
from .._report import archive_plan_to_json_dict, format_archive_report
from ._compat import spec_command_kwargs


@click.command(
    "backup",
    **spec_command_kwargs(
        summary="Copy a directory to cold storage verified; keep the local copy.",
        description=(
            "Pushes SOURCE to --to via rsync-over-ssh (checksummed by "
            "default), verifies the copy with an independent read-back, "
            "writes a manifest marked retained_source, and stops. The "
            "source is never removed by this verb. "
            "Defaults to a dry-run (--dry-run is accepted explicitly too).",
        ),
        examples=(
            ("{prog} backup ~/proj/paper --to scitex-nas-02", "dry-run"),
            (
                "{prog} backup ~/proj/paper --to scitex-nas-02 --yes",
                "copy, verify, keep local copy",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path())
@click.option(
    "--to",
    "destination",
    type=click.Choice(tuple(DESTINATIONS) + tuple(RETIRED_DESTINATIONS)),
    required=True,
    help="Backup target alias.",
)
@click.option(
    "--remote-path",
    default=None,
    help="Remote path on the destination. Default: mirror SOURCE's "
    "absolute path under ~/scitex-storage-archive.",
)
@click.option(
    "--exclude",
    "exclude_patterns",
    multiple=True,
    metavar="PATTERN",
    help="Glob pattern to exclude from the sync (repeatable).",
)
@click.option(
    "--checksum/--no-checksum",
    default=True,
    show_default=True,
    help="Verify every byte via rsync --checksum during the copy.",
)
@click.option(
    "--verify-content/--no-verify-content",
    "verify_content_too",
    default=False,
    show_default=True,
    help="Re-hash both trees INDEPENDENTLY of rsync after the sync, and "
    "record the content digest baseline for later scrubs. Costs a full "
    "re-read of both sides.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only (explicit spelling of the default).",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Actually sync + verify. Without this flag, backup only reports the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def backup_cmd(
    source: str,
    destination: str,
    remote_path: str | None,
    exclude_patterns: tuple[str, ...],
    checksum: bool,
    verify_content_too: bool,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    """Copy SOURCE to cold storage verified; the local copy stays."""
    _require_rsync()
    source_destination = destination
    destination, notice = resolve_destination(destination)
    if notice and not as_json:
        click.echo(f"NOTE: {notice}", err=True)
    do_apply = confirmed and not dry_run
    probe = probe_transport(destination)
    if not probe.may_transport:
        if do_apply:
            raise click.ClickException(
                f"transport to {destination!r} is {probe.verdict}; refusing to "
                f"sync.\n\n{probe.detail}\n\n"
                "The source is untouched. Fix the transport and re-run -- or "
                f"check it directly with:  ssh {destination} true"
            )
        if not as_json:
            click.echo(
                f"TRANSPORT {probe.verdict.upper()}: {destination} -- the plan "
                f"below is a MEASUREMENT OF THE SOURCE, not a prediction that "
                f"the run would succeed.\n{probe.detail}",
                err=True,
            )
    plan = plan_archive(source, destination, remote_path=remote_path)
    manifest = (
        apply_archive(
            plan,
            checksum=checksum,
            exclude=exclude_patterns,
            verify_content_too=verify_content_too,
            remove_source=False,
        )
        if do_apply
        else None
    )
    if as_json:
        payload = archive_plan_to_json_dict(plan, applied=do_apply, manifest=manifest)
        if notice:
            payload["destination_rewritten_from"] = source_destination
            payload["destination_notice"] = notice
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(format_archive_report(plan, applied=do_apply, manifest=manifest))
