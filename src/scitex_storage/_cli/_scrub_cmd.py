#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage scrub`` — is the cold copy still what the manifest says?

WHY THIS VERB EXISTS. An archive is verified ONCE, at migration time.
After that the destination sits on a NAS while disks age and other hands
touch the share -- and the next read is the recall, exactly when a failure
hurts most. Scrub re-measures the destination against the manifest on
demand, without needing the source (usually long deleted).

IT NEVER WRITES ANYTHING, anywhere. Read-only, always: no --yes flag
exists because there is nothing to confirm. Exit codes report, they do
not license -- nothing in this package deletes on a scrub verdict.

EXIT CODES, in their own range like every other verdict verb (`survey`
owns 12/13, `verify-content` 14/15, this owns 16/17):

  0   tally verified, and content verified / not requested / unknown-baseline
  16  mismatch -- tally or content demonstrably differs; investigate
  17  could-not-look -- a probe did not run; do NOT read this as healthy

Content "unknown" (tally-cleared manifest, no baseline) exits 0 WHEN the
tally verified, and says so LOUDLY in both text and JSON: no baseline is
a manifest property, not a destination fault, and failing the run would
train operators to ignore the code. A scrub that cannot speak about
content is still a scrub that counted every entry.
"""

from __future__ import annotations

import json

import click

from .._transfer._archive import ArchiveManifest
from .._transfer._scrub import scrub_manifest
from .._transfer._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED
from ._compat import spec_command_kwargs

#: Declared numeric codes with documented meanings (see module docstring).
EXIT_CODES = {
    VERIFIED: 0,
    MISMATCH: 16,
    COULD_NOT_LOOK: 17,
}


def _load_manifest(path: str) -> ArchiveManifest:
    """Read a manifest file or fail as a usage error (exit 2)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return ArchiveManifest.from_dict(data)
    except FileNotFoundError:
        raise click.BadParameter(f"manifest not found: {path}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise click.BadParameter(f"manifest is not valid JSON: {path} ({exc})")
    except TypeError as exc:
        raise click.BadParameter(f"manifest is not an archive manifest: {path} ({exc})")


@click.command(
    "scrub",
    **spec_command_kwargs(
        summary="Re-verify an archived tree against its manifest (read-only).",
        description=(
            "Months after an archive, re-measure the destination and compare "
            "it with the manifest: entry count + bytes always, per-file "
            "re-hash with --content when the manifest carries a baseline. "
            "Never writes anything, anywhere; exit codes report, they do "
            "not license. Exit codes: 0 verified / 16 mismatch / "
            "17 could-not-look.",
        ),
        examples=(
            (
                "{prog} scrub ~/.scitex/scitex-storage/runtime/archive-manifests/<name>.json",
                "re-count the cold tree against its manifest",
            ),
            (
                "{prog} scrub <manifest> --content --json",
                "also re-hash every entry; machine-readable report",
            ),
        ),
    ),
)
@click.argument("manifest", type=click.Path())
@click.option(
    "--content/--no-content",
    default=False,
    show_default=True,
    help="Re-hash every entry on the destination and compare with the "
    "manifest baseline. Needs a baseline (archive ran --verify-content); "
    "without one, content reports unknown.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON. The exit code is the same either way.",
)
def scrub_cmd(manifest: str, content: bool, as_json: bool) -> None:
    """Re-verify the archived tree named by MANIFEST. Read-only, always."""
    record = _load_manifest(manifest)
    report = scrub_manifest(record, content=content)

    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        click.echo(f"scrub:            {record.source} -> {record.destination}:{record.remote_path}")
        click.echo(f"tally:            {report.tally_verdict}")
        click.echo(
            f"entries:          {report.tally.observed_count} observed / "
            f"{report.tally.expected_count} recorded"
        )
        click.echo(f"content:          {report.content_verdict}")
        click.echo(f"content evidence: {report.content_evidence}")
        click.echo(f"tally evidence:   {report.tally.evidence}")

    if report.tally_verdict == MISMATCH or report.content_verdict == MISMATCH:
        raise SystemExit(EXIT_CODES[MISMATCH])
    if (
        report.tally_verdict == COULD_NOT_LOOK
        or report.content_verdict == COULD_NOT_LOOK
    ):
        raise SystemExit(EXIT_CODES[COULD_NOT_LOOK])
    raise SystemExit(EXIT_CODES[VERIFIED])

# EOF
