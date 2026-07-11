#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage archive`` / ``restore`` — move-not-delete nas/nas2 tiering."""

from __future__ import annotations

import json

import click

from .._archive import DESTINATIONS, apply_archive, apply_restore, plan_archive, plan_restore
from .._report import (
    archive_plan_to_json_dict,
    format_archive_report,
    format_restore_report,
    restore_plan_to_json_dict,
)
from ._compat import spec_command_kwargs

# "archive"/"restore" are ecosystem-canonical mutating verbs (scitex-dev
# audit-cli §2 / the universal-flags convention doc), which requires a
# literal --dry-run + --yes/-y pair rather than this repo's --apply idiom
# used on prune/sweep (not in the audit's mutating-verb list, so not
# forced there -- --apply stays as-is on those, unchanged, to avoid a
# breaking rename on already-shipped flags). --dry-run is a redundant-but-
# present explicit spelling of the default (dry-run already happens when
# neither flag is given, matching this package's "every mutating command
# defaults to a dry-run" doctrine); --yes/-y is the actual mutation gate.


@click.command(
    "archive",
    **spec_command_kwargs(
        summary="Move a directory to nas/nas2 over ssh, verify, then remove locally.",
        description=(
            "Pushes SOURCE to --to (nas or nas2) via scitex-ssh's sync_dir "
            "(rsync-over-ssh), verifies the sync succeeded (checksummed by "
            "default -- reads every byte on both sides), writes a manifest "
            "under ~/.scitex/scitex-storage/runtime/archive-manifests/, and "
            "ONLY THEN removes the local SOURCE. A failed sync leaves "
            "SOURCE completely untouched and no manifest is written. "
            "Defaults to a dry-run (--dry-run is accepted explicitly too).",
        ),
        examples=(
            ("{prog} archive ~/proj/old-experiment --to nas2", "dry-run"),
            (
                "{prog} archive ~/proj/old-experiment --to nas2 --yes",
                "sync, verify, remove local copy",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path())
@click.option(
    "--to",
    "destination",
    type=click.Choice(DESTINATIONS),
    required=True,
    help="Archive target alias.",
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
    help="Verify every byte via rsync --checksum before removing the local copy.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only (explicit spelling of the default -- no side effects either way).",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Actually sync + remove. Without this flag, archive only reports the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def archive_cmd(
    source: str,
    destination: str,
    remote_path: str | None,
    exclude_patterns: tuple[str, ...],
    checksum: bool,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    do_apply = confirmed and not dry_run
    plan = plan_archive(source, destination, remote_path=remote_path)
    manifest = (
        apply_archive(plan, checksum=checksum, exclude=exclude_patterns)
        if do_apply
        else None
    )
    if as_json:
        click.echo(
            json.dumps(
                archive_plan_to_json_dict(plan, applied=do_apply, manifest=manifest),
                indent=2,
            )
        )
    else:
        click.echo(format_archive_report(plan, applied=do_apply, manifest=manifest))


@click.command(
    "restore",
    **spec_command_kwargs(
        summary="Pull an archived directory back from nas/nas2 to its original path.",
        description=(
            "Reads the manifest `archive` wrote for SOURCE and pulls the "
            "data back via scitex-ssh's sync_dir. The remote copy is kept "
            "by default -- pass --delete-remote to remove it after a "
            "verified restore. Defaults to a dry-run (--dry-run is "
            "accepted explicitly too).",
        ),
        examples=(
            ("{prog} restore ~/proj/old-experiment", "dry-run"),
            ("{prog} restore ~/proj/old-experiment --yes", "pull it back"),
            (
                "{prog} restore ~/proj/old-experiment --yes --delete-remote",
                "pull back and remove the archive",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path())
@click.option(
    "--delete-remote",
    is_flag=True,
    help="Remove the remote copy after a verified restore.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview only (explicit spelling of the default -- no side effects either way).",
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Actually pull. Without this flag, restore only reports the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def restore_cmd(
    source: str,
    delete_remote: bool,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    do_apply = confirmed and not dry_run
    plan = plan_restore(source)
    restored_path = (
        apply_restore(plan, delete_remote=delete_remote) if do_apply else None
    )
    if as_json:
        click.echo(
            json.dumps(
                restore_plan_to_json_dict(
                    plan, applied=do_apply, restored_path=restored_path
                ),
                indent=2,
            )
        )
    else:
        click.echo(
            format_restore_report(plan, applied=do_apply, restored_path=restored_path)
        )


# EOF
