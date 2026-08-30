#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage archive`` / ``restore`` — move-not-delete NAS tiering."""

from __future__ import annotations

import json

import click

from .._transfer._archive import (
    DESTINATIONS,
    _rsync_binary,
    apply_archive,
    apply_restore,
    plan_archive,
    plan_restore,
)
from .._transfer._archive_transport import (
    RETIRED_DESTINATIONS,
    probe_transport,
    resolve_destination,
)
from .._measure._scan import MissingSystemDependencyError
from .._report import (
    archive_plan_to_json_dict,
    format_archive_report,
    format_restore_report,
    restore_plan_to_json_dict,
)
from ._compat import spec_command_kwargs


def _require_rsync() -> None:
    """Fail loud + clean when the local rsync is missing.

    Converts :class:`MissingSystemDependencyError` into a ClickException so
    the user gets install instructions instead of a traceback -- the same
    treatment `scan` gives a missing `fd` (see ``_scan_cmd.py``).
    """
    try:
        _rsync_binary()
    except MissingSystemDependencyError as exc:
        raise click.ClickException(str(exc)) from exc

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
    # Retired aliases stay ACCEPTED here on purpose. click.Choice rejects
    # before plan_archive is ever called, so listing only the live names would
    # turn `--to nas2` -- which worked yesterday and is in people's scripts --
    # into a usage error instead of a rewrite-with-notice.
    type=click.Choice(tuple(DESTINATIONS) + tuple(RETIRED_DESTINATIONS)),
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
    "--verify-content/--no-verify-content",
    "verify_content_too",
    default=False,
    show_default=True,
    help="Re-hash both trees INDEPENDENTLY of rsync after the sync, and "
    "refuse the delete on any mismatch. Opt-in: rsync --checksum already "
    "reads every byte, so this is a second opinion from a different "
    "instrument, not a first one. Costs a full re-read of both sides.",
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
    verify_content_too: bool,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    # Check the transport BEFORE planning, even on a dry-run. The dry-run is
    # this command's default, and its contract is to predict the real run --
    # so "WOULD ARCHIVE 500 GB" on a box with no rsync is a confident claim
    # about something never checked, and the user only finds out at `--yes`,
    # exactly when they believed the question was settled. The CLI is always
    # the real-transport path (it never injects a `runner`), so requiring the
    # binary here is honest rather than over-strict.
    _require_rsync()
    # SAY the rewrite. plan_archive resolves a retired alias silently and the
    # run works, which is exactly the problem: the caller keeps typing a dead
    # name, learns nothing, and their scripts stay wrong until the rewrite is
    # removed.
    #
    # The notice goes in the PAYLOAD under --json, not to stderr. I wrote it to
    # stderr first, with a comment claiming that "cannot corrupt --json" -- and
    # test_cli_archive_json_reports_the_destination failed on the next run with
    # `JSONDecodeError: line 1 column 1`, because a caller (CliRunner here, any
    # `2>&1` in the wild) may merge the streams. A machine-readable fact
    # belongs in the machine-readable output; a side channel is a place for it
    # to be lost or to land in the middle of the document.
    source_destination = destination
    destination, notice = resolve_destination(destination)
    if notice and not as_json:
        click.echo(f"NOTE: {notice}", err=True)
    do_apply = confirmed and not dry_run
    # THE OTHER HALF OF _require_rsync's PROMISE. That check refuses to plan
    # when the local binary is missing, so a dry-run cannot predict a run whose
    # transport could not start. The REMOTE half went unchecked until
    # 2026-08-11, when every NAS destination returned rc=255 (a read-only
    # ~/.ssh, so no ControlMaster socket could bind) and `archive --to nas2`
    # still printed "WOULD ARCHIVE ... -> nas2:~/..." and exited 0.
    #
    # ASYMMETRIC ON PURPOSE. --yes REFUSES: it is about to sync and then delete
    # the source, and starting that against a transport known to be down risks
    # a partial state for no benefit. The dry-run REPORTS instead of refusing,
    # because the plan itself (size, file count, remote path) is genuinely
    # computable and useful while the transport is down -- what must not
    # survive is the reader's impression that the run WOULD succeed.
    probe = probe_transport(destination)
    if not probe.may_transport:
        if do_apply:
            raise click.ClickException(
                f"transport to {destination!r} is {probe.verdict}; refusing to "
                f"sync + delete.\n\n{probe.detail}\n\n"
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
        )
        if do_apply
        else None
    )
    if as_json:
        payload = archive_plan_to_json_dict(plan, applied=do_apply, manifest=manifest)
        if notice:
            # Present ONLY when a rewrite happened, so a consumer can test for
            # the key rather than compare a sentinel. `destination` already
            # holds the live name, so this says what the caller TYPED and why
            # it changed -- information that is otherwise unrecoverable from
            # the payload.
            payload["destination_rewritten_from"] = source_destination
            payload["destination_notice"] = notice
        click.echo(json.dumps(payload, indent=2))
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
    # Same reasoning as `archive` -- a dry-run must not promise a pull the
    # transport cannot start.
    _require_rsync()
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
