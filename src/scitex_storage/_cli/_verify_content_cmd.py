#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage verify-content`` — is it safe to delete the source yet?

WHY THIS VERB EXISTS, and it is the same gap this package keeps re-opening.
``_content_verify`` shipped as a library one commit ago: hashing, three-state
verdicts, denominators, tests. None of it is reachable by the two consumers
that matter — a human at a shell about to free 9.1 TB, and any script that
wants to gate a delete. A PYTHON API IS NOT A CAPABILITY. `find-recipe` and
`survey` both exist because that lesson had to be learned twice already;
shipping the hashing layer with no verb would have been the third time,
knowingly, in the same week I wrote it down.

IT NEVER DELETES ANYTHING. It answers one question — does the destination
hold, byte for byte, what the source holds — and returns. Whatever acts stays
outside. That is the same layer split as `survey`, and here it is load-bearing
rather than stylistic: the operator's requirement is explicit that moving and
deleting must be SEPARATE RUNS, with a human reading the numbers in between.

    「削除は必ず別の実行。同じ実行の中で『移して消す』をやらない。
      人が数字を見てから消す」

WHY IT REPORTS A DENOMINATOR EVEN WHEN NOTHING IS WRONG. "0 mismatched" is
not a result; "0 of 200,000 mismatched" is. A report that cannot distinguish
those is how a comparison over an empty or invisible tree gets read as
success, and it is the exact shape of the failure this package has hit at
three different layers now.

EXIT CODES, deliberately not 1 or 2, and deliberately in their own range.
1 and 2 already mean "generic failure" and "usage error" in every CLI
framework, so a missing or renamed verb exits 2 and would IMPERSONATE a
verdict — which fired for real on 2026-07-28. `find-recipe` owns 10/11,
`survey` owns 12/13, this owns 14/15, and no number means two things.

  0   verified         every entry matched; a source removal is licensed
  14  mismatch         the destination demonstrably differs; do NOT remove
  15  could-not-look   something could not be read; do NOT remove

14 AND 15 ARE BOTH REFUSALS AND THEY ARE NOT INTERCHANGEABLE. They have the
same consequence for the caller and are different facts for the human: 14
means "I looked and it is wrong", 15 means "I could not look". Collapsing
them would hide the case where a permission error, not a corrupt copy, is
what stands between the operator and their disk space.
"""

from __future__ import annotations

import json

import click

from .._transfer._content_verify import digest_tree, verify_content
from .._transfer._verify import COULD_NOT_LOOK, MISMATCH, VERIFIED
from ._compat import spec_command_kwargs

#: Declared numeric codes with documented meanings. See the module docstring
#: for why 1 and 2 are reserved and why these differ from `survey`'s.
EXIT_CODES = {
    VERIFIED: 0,
    MISMATCH: 14,
    COULD_NOT_LOOK: 15,
}


def verdict_to_dict(verdict, source: str, destination: str) -> dict:
    """The wire shape. Every key present on every call.

    A caller must never have to guess which key exists on this run. The byte
    fields are carried through as ``null`` rather than omitted, because this
    check does not measure bytes and a MISSING key and a key meaning "not
    measured" would be indistinguishable to a consumer reading JSON.
    """
    return {
        "source": source,
        "destination": destination,
        "verdict": verdict.verdict,
        "may_remove_source": verdict.may_remove_source,
        "expected_count": verdict.expected_count,
        "observed_count": verdict.observed_count,
        "expected_bytes": verdict.expected_bytes,
        "observed_bytes": verdict.observed_bytes,
        "evidence": verdict.evidence,
    }


@click.command(
    "verify-content",
    **spec_command_kwargs(
        summary="Compare a destination to its source by sha256; say if the source may go.",
        description=(
            "The STRICT check, for the step before an irreversible delete. "
            "`archive`/`sweep` verify a transfer by entry COUNT and byte "
            "TOTAL, which is the right cheap check to watch a transfer with "
            "and cannot see a file of exactly the right length whose contents "
            "are wrong. This one hashes every entry on both sides.",
            "It NEVER deletes anything. Deletion is a separate run, started "
            "by a human who has read these numbers.",
            "Exit codes: 0 verified (removal licensed) / 14 mismatch / "
            "15 could-not-look. 14 and 15 are both refusals and are NOT "
            "interchangeable: 14 means the destination is wrong, 15 means "
            "something could not be read. 1 and 2 are reserved so a missing "
            "verb cannot impersonate a verdict.",
        ),
        examples=(
            (
                "{prog} verify-content /share/nas2/WORK/data /mnt/usb-hdd/data",
                "hash both trees and report whether the source may be removed",
            ),
            (
                "{prog} verify-content SRC DST --json",
                "machine-readable verdict; same exit code either way",
            ),
        ),
    ),
)
@click.argument("source", type=click.Path())
@click.argument("destination", type=click.Path())
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the verdict as JSON. The exit code is the same either way.",
)
def verify_content_cmd(source: str, destination: str, as_json: bool) -> None:
    """Compare DESTINATION against SOURCE by sha256, entry by entry.

    Answers whether the source may be deleted. It does not delete it.

    This is the STRICT check. `verify_transfer`'s count-and-size comparison is
    the right cheap check to watch a transfer with, and it cannot see a file
    of the right length whose contents are wrong -- which is precisely the
    case that matters when the next step is irreversible.
    """
    verdict = verify_content(digest_tree(source), digest_tree(destination))
    payload = verdict_to_dict(verdict, source, destination)

    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(f"verdict:          {verdict.verdict}")
        click.echo(f"may remove source: {verdict.may_remove_source}")
        # ALWAYS PRINT THE DENOMINATOR, including on a pass. A reader who sees
        # only "verified" cannot tell whether it compared 200,000 entries or
        # none, and the second is the failure this whole package is built
        # against.
        click.echo(
            f"entries:          {verdict.observed_count} observed / "
            f"{verdict.expected_count} expected"
        )
        click.echo(f"evidence:         {verdict.evidence}")

    raise SystemExit(EXIT_CODES[verdict.verdict])

# EOF
