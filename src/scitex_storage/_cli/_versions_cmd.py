"""Local additive data versions; CLI writes require --yes, never delete/GC."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import click
import scitex_logging as slogging

from .._versions import ChunkingConfig, VersionStore, write_git_pointer
from ._compat import spec_command_kwargs, spec_group_kwargs

_console = slogging.getPlainConsole(__name__)


def _emit(value):
    _console.emit(json.dumps(value, sort_keys=True))


def _run(action):
    try:
        return action()
    except (OSError, ValueError, RuntimeError) as exc:
        _emit(
            {"status": "refused", "error": str(exc), "error_type": type(exc).__name__}
        )
        raise click.exceptions.Exit(1) from exc


@click.group(
    "versions",
    **spec_group_kwargs(
        summary="Additive local content versions and plain Git pointers.",
        description=(
            "Storage owns immutable chunks/manifests; normal Git owns pointer "
            "history. This is not Git LFS. No GC or source deletion is "
            "performed."
        ),
    ),
)
@click.option(
    "--store",
    type=click.Path(path_type=Path),
    required=True,
    help="Local immutable object-store directory.",
)
@click.pass_context
def versions_group(ctx, store):
    ctx.obj = _run(lambda: VersionStore(store))


@versions_group.command(
    "add",
    **spec_command_kwargs(
        summary="Snapshot a regular file without changing it.",
        description=(
            "Defaults to stat-only preview. --yes reads contents with bounded "
            "native FastCDC and adds immutable SHA256 chunks plus a version "
            "manifest. --dry-run overrides --yes."
        ),
        examples=(
            (
                "{prog} versions --store ./objects add ./experiment.bin --yes",
                "add a local snapshot; retain source",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--min-size",
    type=click.IntRange(min=1),
    default=ChunkingConfig().min_size,
    show_default=True,
)
@click.option(
    "--average-size",
    type=click.IntRange(min=1),
    default=ChunkingConfig().average_size,
    show_default=True,
)
@click.option(
    "--max-size",
    type=click.IntRange(min=1),
    default=ChunkingConfig().max_size,
    show_default=True,
)
@click.option("--yes", is_flag=True, help="Apply this additive snapshot.")
@click.option("--dry-run", is_flag=True, help="Preview, even when --yes is present.")
@click.pass_obj
def add_cmd(store, source, min_size, average_size, max_size, yes, dry_run):
    def action():
        configured = VersionStore(
            store.root, chunking=ChunkingConfig(min_size, average_size, max_size)
        )
        if not yes or dry_run:
            _emit(configured.plan_ingest(source))
            return
        result = configured.ingest(source)
        _emit({"status": "stored", **asdict(result)})

    _run(action)


@versions_group.command(
    "inspect",
    **spec_command_kwargs(
        summary="Verify and inspect a version manifest without writing.",
        examples=(
            (
                "{prog} versions --store ./objects inspect VERSION_ID",
                "read-only metadata verification",
            ),
        ),
        description=(
            "Checks manifest digest, structure, reference sizes/counts; does "
            "not read or claim verification of chunk contents."
        ),
    ),
)
@click.argument("version_id")
@click.pass_obj
def inspect_cmd(store, version_id):
    _run(
        lambda: _emit(
            {
                "status": "manifest_verified",
                "content_verified": False,
                **asdict(store.read_manifest(version_id)),
            }
        )
    )


@versions_group.command(
    "checkout",
    **spec_command_kwargs(
        summary="Reconstruct a verified version into a new file.",
        examples=(
            (
                "{prog} versions --store ./objects checkout VERSION_ID "
                "./restored.bin --yes",
                "verify and create a new file",
            ),
        ),
        description=(
            "Defaults to preview. --yes verifies every chunk and the full file "
            "before atomic no-overwrite publication. Existing files/symlinks "
            "are always refused."
        ),
    ),
)
@click.argument("version_id")
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--yes", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.pass_obj
def checkout_cmd(store, version_id, destination, yes, dry_run):
    def action():
        manifest = store.read_manifest(version_id)
        if yes and not dry_run:
            path = store.checkout(version_id, destination)
            _emit(
                {
                    "status": "checked_out",
                    "destination": str(path),
                    "file_sha256": manifest.file_sha256,
                    "size": manifest.size,
                }
            )
        else:
            _emit(
                {
                    "status": "planned",
                    "destination": str(destination),
                    **asdict(manifest),
                }
            )

    _run(action)


@versions_group.command(
    "pointer",
    **spec_command_kwargs(
        summary="Create a small ordinary Git pointer file.",
        examples=(
            (
                "{prog} versions --store ./objects pointer VERSION_ID "
                "./dataset.stx.json --yes",
                "create a new Git-trackable pointer",
            ),
        ),
        description=(
            "Defaults to preview. --yes creates a new SciTeX pointer; never "
            "overwrites a file, runs Git, installs LFS filters, or connects to "
            "a server."
        ),
    ),
)
@click.argument("version_id")
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--yes", is_flag=True)
@click.option("--dry-run", is_flag=True)
@click.pass_obj
def pointer_cmd(store, version_id, destination, yes, dry_run):
    def action():
        manifest = store.read_manifest(version_id)
        if yes and not dry_run:
            path = write_git_pointer(destination, manifest)
            _emit(
                {
                    "status": "pointer_created",
                    "path": str(path),
                    "version_id": version_id,
                }
            )
        else:
            _emit(
                {
                    "status": "planned",
                    "path": str(destination),
                    "version_id": version_id,
                }
            )

    _run(action)
