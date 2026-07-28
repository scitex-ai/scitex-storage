#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage survey`` — can this tree move, and what says so?

WHY THIS VERB EXISTS, and it is the same gap twice in one week. Layer 1's
eight movability signals were all built, tested and merged; ``classify()``
combined them; ``survey()`` composed them. All of it was Python API, and a
Python API IS NOT A CAPABILITY for the two consumers that matter here: a
shell script on a compute node, and the GUI that has to render a row per
tree. `find-recipe` exists because I shipped four PRs of detector that no
consumer could invoke. Shipping the composition layer without a verb would
have repeated it knowingly.

WHAT IT ANSWERS, and what it deliberately does NOT. It runs the PER-TREE
probes — coldness (a reader leaves no mtime, so atime is read too), open
handles (with a positive control, because a blind /proc scan returns
"nothing is holding this"), readability, and coverage. It does NOT run the
per-move signals (destination reality, free space): those are meaningless
without a destination, and asking them of a source tree is a category
error whose honest answer is "unknown" — which would make every verdict
could-not-look and produce a classifier that always abstains. That failure
is worse than one that always passes, because it LOOKS like caution and
nobody files a bug against a tool for declining to guess.

IT NEVER MOVES OR DELETES ANYTHING. It answers one question and returns.
Whatever acts — a human, a sweep, an approval in the GUI — stays outside,
which is the layer split this package is built around.

EXIT CODES, deliberately not 1 or 2, for the same reason `find-recipe`
avoids them: those already mean "generic failure" and "usage error" in
every CLI framework, so a missing or renamed verb exits 2 and would
IMPERSONATE a verdict. That fired for real on 2026-07-28 — `find-recipe`
was not installed, exited 2, and had 2 carried a domain meaning the
consumer would have read a plausible answer from a command that does not
exist.

  0   movable          nothing this scan can see is standing on it
  12  not-movable      something IS standing on it
  13  could-not-look   a probe could not run, or the map is incomplete

12/13 rather than 10/11 so a caller that shells BOTH verbs cannot confuse
their verdicts: `find-recipe` owns 10/11, this owns 12/13, and no number
means two things.
"""

from __future__ import annotations

import json

import click

from .._classify import COULD_NOT_LOOK, MOVABLE, NOT_MOVABLE, Classification
from .._survey import DEFAULT_COLD_AFTER_SECONDS, survey
from ._compat import spec_command_kwargs

#: Declared numeric codes with documented meanings. See the module
#: docstring for why 1 and 2 are reserved and why these differ from
#: `find-recipe`'s.
EXIT_CODES = {
    MOVABLE: 0,
    NOT_MOVABLE: 12,
    COULD_NOT_LOOK: 13,
}


def classification_to_dict(result: Classification) -> dict:
    """The wire shape. Every key present on every call.

    ``signals`` is a LIST OF OBJECTS rather than a flattened string,
    because the GUI has to render each signal beside its own evidence, and
    a consumer must never have to parse prose to find out which probe
    said what. Each carries its own verdict, so a caller can see that a
    tree was refused by ONE signal while three others were content — the
    disagreement is the information, and any summarising would bury it.
    """
    return {
        "path": result.path,
        "verdict": result.verdict,
        "signals": [
            {"name": s.name, "verdict": s.verdict, "evidence": s.evidence}
            for s in result.signals
        ],
        "reason": result.reason,
    }


@click.command(
    "survey",
    **spec_command_kwargs(
        summary="Can this tree move, and what evidence says so?",
        description=(
            "Runs the mechanical PER-TREE probes over PATH and prints a "
            "three-state verdict with the evidence that produced it: movable "
            "(nothing this scan can see is standing on it), not-movable "
            "(something is), or could-not-look (a probe could not run, or "
            "part of the tree was unreadable so the map is incomplete). It "
            "NEVER moves or deletes anything.\n\n"
            "It does NOT check whether a DESTINATION exists or has room — "
            "those questions need a destination and belong to `archive`, "
            "`reclaim` and `sweep`, which ask them before they move data. "
            "Nor does it decide whether a tree is WORTH keeping; that is a "
            "value question no measurement answers. Use `find-recipe` for "
            "the separate question of whether deleting this would lose "
            "anything.\n\n"
            "COULD-NOT-LOOK IS NOT A SOFT NO. It means the question was not "
            "answered, and a sweep should stop and re-measure rather than "
            "treat it as permission or as refusal.\n\n"
            "Exit codes: 0 movable, 12 not-movable, 13 could-not-look — "
            "never 1 or 2, so a missing verb cannot impersonate a verdict."
        ),
        examples=(
            (
                "{prog} survey /data/gpfs/projects/punim0264/capsule-045 --json",
                "classify one tree and emit the evidence for a machine",
            ),
            (
                "{prog} survey /mnt/nas2/corpus --cold-after-days 30",
                "treat a tree as cold only after a month without a read",
            ),
        ),
    ),
)
@click.argument("path", type=click.Path())
@click.option(
    "--cold-after-days",
    default=DEFAULT_COLD_AFTER_SECONDS / 86400,
    type=float,
    show_default=True,
    help=(
        "Days without a read OR a write before a tree counts as cold. The "
        "default is a fortnight rather than a week because a fortnight of "
        "silence is what a holiday or a conference produces from a corpus "
        "that is very much still wanted."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.pass_context
def survey_cmd(ctx, path, cold_after_days, as_json):
    result = survey(path, cold_after_seconds=cold_after_days * 86400)
    payload = classification_to_dict(result)

    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(f"{result.verdict}: {result.path}")
        # EVERY signal is printed, not just the deciding ones. The reason
        # five reclaim candidates were correctly withdrawn on 2026-07-22
        # is that someone read the evidence; a display that shows only the
        # conclusion optimises for the wrong thing.
        for signal in result.signals:
            click.echo(f"  [{signal.verdict}] {signal.name}: {signal.evidence}")

    ctx.exit(EXIT_CODES[result.verdict])

# EOF
