#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage CLI — thin orchestrator; each verb lives in its own submodule."""

from __future__ import annotations

import sys

import click

from .. import __version__
from ._archive_cmd import archive_cmd, restore_cmd
from ._compat import spec_group_kwargs
from ._document_sorter_cmd import document_sorter_group
from ._duplicates_cmd import find_duplicates_cmd
from ._fleet_status_cmd import fleet_status_cmd
from ._gui_cmd import gui_group, start_gui_cmd
from ._images_cmd import images_group
from ._inodes_cmd import inodes_cmd
from ._introspect import list_python_apis
from ._mcp_commands import mcp
from ._reclaim_cmd import reclaim_cmd, reclaim_restore_cmd
from ._regenerable_cmd import regenerable_cmd
from ._survey_cmd import survey_cmd
from ._scan_cmd import scan_cmd
from ._sweep_cmd import sweep_cmd, sweep_status_cmd

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def _print_command_help(cmd, prefix: str, parent_ctx) -> None:
    click.echo(f"\n{'=' * 50}")
    click.echo(prefix)
    click.echo("=" * 50)
    sub_ctx = click.Context(cmd, info_name=prefix.split()[-1], parent=parent_ctx)
    click.echo(cmd.get_help(sub_ctx))
    if isinstance(cmd, click.Group):
        for sub_name, sub_cmd in sorted(cmd.commands.items()):
            _print_command_help(sub_cmd, f"{prefix} {sub_name}", sub_ctx)


@click.group(
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
    **spec_group_kwargs(
        summary="Research-data storage triage (scan + duplicates + versioned-image rotation).",
        description=(
            "Discovery + rotation layers of a planned storage-tiering tool "
            "(local SSD -> NAS SSD -> NAS HDD -> offline). `scan` is a "
            "read-only, stat-only directory walk (via `fd`) that reports "
            "the biggest space and inode (file-count) consumers per "
            "top-level child of a root -- no file contents are ever read. "
            "`validate-inodes` is the cheap counterpart: one statvfs per path, no "
            "walk and no system binaries, reporting how close a filesystem "
            "is to inode exhaustion (which fails every write while df still "
            "shows free space). "
            "`find-duplicates` is a separate, explicitly opt-in verb (via "
            "`fclones`) that DOES read file contents (to hash them) to "
            "report exact-duplicate groups. `images prune` rotates a "
            "directory of versioned files (e.g. dated SIF builds), always "
            "excluding files any symlink in the directory currently "
            "references. `sweep` tars an inode-hog directory in place "
            "(many small files -> one tar, one inode), compute-node-only, "
            "gated on an explicit per-directory confirm. `archive` moves a "
            "directory to nas/nas2 over ssh (scitex-ssh's sync_dir), "
            "verifying before removing the local copy and writing a "
            "manifest `restore` reads back. `reclaim` moves a path aside "
            "into a reversible local archive (default adjacent `.old/`, or "
            "--archive-root on another filesystem to free inodes) instead of "
            "deleting it, so a rough cleanup call costs only a "
            "`reclaim-restore`; the fraction of runs restored is reported as "
            "the honest accuracy metric. `fleet-status` renders a "
            "self-contained HTML dashboard of every host's space% and inode% "
            "grouped by role/tier, carrying the same three-state verdicts "
            "(measured / not-applicable / could-not-look) so a filesystem it "
            "could not read is never shown as a reassuring green 0%. Every "
            "mutating command defaults to a dry-run.",
        ),
        config_resolution=(
            "scitex-storage has no configurable state yet — every command "
            "takes its options on the command line. Runtime state written: "
            "archive manifests under "
            "~/.scitex/scitex-storage/runtime/archive-manifests/, one JSON "
            "file per archived directory, read back by `restore`.",
        ),
        version_of="scitex-storage",
        command_categories=(
            (
                "Storage",
                (
                    "scan",
                    "validate-inodes",
                    "find-duplicates",
                    "images",
                    "sweep",
                    "sweep-status",
                    "archive",
                    "restore",
                    "reclaim",
                    "reclaim-restore",
                    "fleet-status",
                ),
            ),
            ("Documents", ("document-sorter",)),
            ("GUI", ("gui",)),
            ("Introspection", ("list-python-apis", "mcp")),
        ),
    ),
)
@click.version_option(
    __version__,
    "-V",
    "--version",
    prog_name="scitex-storage",
    message="%(prog)s %(version)s",
)
@click.option("--help-recursive", is_flag=True, help="Show help for all commands.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit structured JSON output (propagates to subcommands that honour it).",
)
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json

    if help_recursive:
        click.echo(f"scitex-storage {__version__}")
        click.echo(main.get_help(ctx))
        for name, cmd in sorted(main.commands.items()):
            _print_command_help(cmd, f"scitex-storage {name}", ctx)
        ctx.exit(0)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


main.add_command(scan_cmd)
main.add_command(inodes_cmd)
main.add_command(find_duplicates_cmd)
main.add_command(regenerable_cmd)
main.add_command(survey_cmd)
main.add_command(images_group)
main.add_command(sweep_cmd)
main.add_command(sweep_status_cmd)
main.add_command(archive_cmd)
main.add_command(restore_cmd)
main.add_command(reclaim_cmd)
main.add_command(reclaim_restore_cmd)
main.add_command(document_sorter_group)
main.add_command(fleet_status_cmd)
main.add_command(list_python_apis)
main.add_command(mcp)
main.add_command(gui_group)
main.add_command(start_gui_cmd)

# §1a: install-shell-completion + print-shell-completion (canonical leaves).
try:
    from scitex_dev._cli._completion import attach_shell_completion

    attach_shell_completion(main, prog_name="scitex-storage")
except ImportError:
    pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# EOF
