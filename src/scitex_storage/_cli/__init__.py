#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-storage CLI — thin orchestrator; each verb lives in its own submodule."""

from __future__ import annotations

import sys

import click

from .. import __version__
from ._compat import spec_group_kwargs
from ._lazy import make_lazy_group

# NO EAGER VERB IMPORTS. Every verb used to be imported here, which coupled
# every verb to every other verb's dependencies -- one stale sibling package
# disabled the entire CLI, including verbs that never touch it. Measured in
# the real solver image on Spartan: a stale `scitex_ssh` (missing `sync_dir`)
# took out `survey` and `find-recipe`, neither of which uses SSH, and exited
# 1. See `_lazy.py` for the full account and the reserved exit code.

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


_GROUP_KWARGS = spec_group_kwargs(
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
)

# The base group class is not ours to pick: `spec_group_kwargs` supplies
# scitex-dev's SpecGroup when its help helpers are installed, and nothing
# when they are not. Pop it and mix lazy loading OVER it, so the spec-help
# rendering survives on machines that have it.
_BASE_GROUP_CLS = _GROUP_KWARGS.pop("cls", None)


@click.group(
    cls=make_lazy_group(_BASE_GROUP_CLS),
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
    **_GROUP_KWARGS,
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
        # Goes through get_command so verbs load on demand. This is the one
        # path that deliberately imports EVERYTHING -- the caller asked for
        # every command's help. A verb whose module is broken still renders,
        # as the self-describing UNAVAILABLE stand-in rather than a traceback.
        for name in main.list_commands(ctx):
            cmd = main.get_command(ctx, name)
            if cmd is not None:
                _print_command_help(cmd, f"scitex-storage {name}", ctx)
        ctx.exit(0)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Verbs are NOT attached here any more -- they are declared as data in
# `_lazy.VERB_REGISTRY` and imported by `LazyGroup.get_command` on first use.
# Adding a verb means adding one registry line, and it can no longer break
# unrelated verbs at import time.
#
# TWO EXCEPTIONS, attached eagerly on purpose. The CLI-conventions audit
# (§1a) requires `list-python-apis` and an `mcp` group to be DISCOVERABLE,
# and it introspects the attached-command mapping rather than calling
# `list_commands()`/`get_command()`, so lazily-registered verbs are invisible
# to it. Attaching these two costs nothing and reintroduces no coupling:
# both import only stdlib, click, `._compat` and `scitex_storage` itself --
# verified, not assumed. The failure this module exists to prevent came in
# through `_archive_cmd` -> `_archive` -> `scitex_ssh`, an EXTERNAL sibling
# package, and neither of these touches one.
from ._introspect import list_python_apis  # noqa: E402
from ._mcp_commands import mcp  # noqa: E402

main.add_command(list_python_apis)
main.add_command(mcp)

# §1a: install-shell-completion + print-shell-completion (canonical leaves).
try:
    from scitex_dev._cli._completion import attach_shell_completion

    attach_shell_completion(main, prog_name="scitex-storage")
except ImportError:
    pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# EOF
