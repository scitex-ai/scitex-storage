#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage find-recipe`` — is this tree rebuildable, and from what?

NAMED `find-recipe`, NOT `regenerable`, and the rename happened AFTER the
contract had been sent to its consumer -- so it is worth saying why rather
than quietly shipping it. The CLI audit rejected `regenerable`: it is an
ADJECTIVE, and the lexicon admits nouns, transitive verbs and intransitive
verbs only. There is no `adjectives` category to declare it under, so the
options were to force it into a category it does not belong to -- which is
configuring a check to pass rather than fixing the name -- or to rename.

`find-recipe` is a real verb plus a real noun, matches this package's own
`find-duplicates`, and describes the actual mechanic: look for the recipe
that would rebuild this tree. The verdicts read coherently against it --
`regenerable` (a recipe was found), `cache` (no recipe needed, the recipe
is the network), `not-regenerable` (no recipe, so this is the only copy).

The JSON keys and exit codes are UNCHANGED. Only the command name moved.

WHY THIS VERB EXISTS AT ALL, recorded because the gap was embarrassing:
:mod:`scitex_storage._regenerable` was built, tested, merged and described
to its intended consumer as usable — while being unreachable from the
process that needs it. paper-scitex-clew measured what I had not
(2026-07-28): ``scitex_storage`` is not importable in their container, and
no CLI verb existed. Their consumer is a bash function sourced by a cohort
launcher inside a solver container on a Spartan compute node. A Python API
is not a capability for that caller. A CAPABILITY CLAIM IS A MEASUREMENT.

WHY A VERB RATHER THAN A SHIM IN THEIR REPO: a shim means they re-derive
this module's semantics on their side, and the two drift silently the
first time the verdict classes change. They changed TWICE on the day this
was written (CACHE added; could-not-look split into a reason). A shim
would have needed updating both times and nothing would have said so. A
declared JSON shape across a process boundary is the contract; a
reimplementation is a copy that rots.

THE OUTPUT IS A FIXED SHAPE. Every key is present on every call, with
``null`` where a field does not apply, so a caller never has to ask
whether a key exists — the same rule the dataclass follows in-process.

EXIT CODES ARE DECLARED, AND DELIBERATELY NOT 1 OR 2:

* ``0``  regenerable — a recipe exists and resolves
* ``0``  cache       — disposable, no spec required
* ``10`` not-regenerable
* ``11`` could-not-look

1 and 2 already mean "generic failure" and "usage error" in every CLI
framework, so a renamed or missing verb exits 2 and would IMPERSONATE a
real verdict. Choosing 10/11 means a caller cannot confuse "the tool is
not there" with "the tool answered". A caller that only checks ``$? -eq
0`` gets the safe reading: anything that is not a positive verdict is
non-zero. The cache/regenerable distinction is in the JSON, not the code,
because two verdicts that license the same action should not need two
exit codes to say so.

THIS VERB NEVER DELETES ANYTHING. It answers one question and returns.
Whatever acts on the answer — a success gate, a human, a sweep — stays
outside, which is the layer split this package is built around.
"""

from __future__ import annotations

import json

import click

from .._measure._regenerable import (
    CACHE,
    COULD_NOT_LOOK,
    NOT_REGENERABLE,
    REGENERABLE,
    RegenerableVerdict,
    is_regenerable,
)
from ._compat import spec_command_kwargs

#: Verdict -> process exit code. See the module docstring for why these
#: are 10/11 rather than 1/2.
EXIT_CODES: dict[str, int] = {
    REGENERABLE: 0,
    CACHE: 0,
    NOT_REGENERABLE: 10,
    COULD_NOT_LOOK: 11,
}


def _provenance(verdict: RegenerableVerdict) -> str | None:
    """Was the recipe DISCOVERED or SUPPLIED? ``None`` when there is none.

    Exposed as its own field rather than left for the caller to grep out
    of the evidence string: "we located this" and "we were told this"
    warrant different trust, and a machine consumer cannot read prose.
    """
    if not verdict.spec_path:
        return None
    return "supplied" if "SUPPLIED BY THE CALLER" in verdict.evidence else "discovered"


def verdict_to_dict(verdict: RegenerableVerdict) -> dict:
    """The wire shape. Every key present on every call."""
    return {
        "path": verdict.path,
        "verdict": verdict.verdict,
        "ecosystem": verdict.ecosystem,
        "marker": verdict.marker,
        "spec_path": verdict.spec_path,
        "spec_provenance": _provenance(verdict),
        "reason": verdict.reason,
        "evidence": verdict.evidence,
    }


@click.command(
    "find-recipe",
    **spec_command_kwargs(
        summary="Is PATH rebuildable, and from what recipe?",
        description=(
            "Answers ONE question about a directory, mechanically and "
            "without judgement: regenerable (a named recipe exists and "
            "resolves), cache (disposable with no spec required, because "
            "the recipe is the network), not-regenerable (no recipe — so "
            "this is the only copy), or could-not-look (the question was "
            "not answered). It NEVER deletes, moves or modifies anything. "
            "Whether something MAY be deleted is a policy decision for the "
            "caller, who knows what the record consists of; this verb only "
            "tells you whether it could be rebuilt. Exit codes are 0 for "
            "regenerable/cache, 10 for not-regenerable, 11 for "
            "could-not-look — never 1 or 2, so a missing verb cannot "
            "impersonate a verdict."
        ),
        # NOTE the spaces after `find-recipe`. These two examples shipped in
        # 0.3.0 as `find-recipe/path/...` and `find-recipeENV` -- un-runnable
        # if copy-pasted. Cause: the `regenerable` -> `find-recipe` rename was
        # applied as a substitution that consumed the trailing space. The
        # rename was verified against the JSON keys and the exit codes (the
        # machine contract) and not against the text a human copies, which is
        # the half a consumer actually starts from. Tested now, not just read.
        examples=(
            (
                "{prog} find-recipe /path/to/capsule/pylibs --stop-at /path/to/capsule --json",
                "classify one env tree, bounded to its capsule",
            ),
            (
                "{prog} find-recipe /path/to/capsule/ENV --spec /corpus/env/Dockerfile --json",
                "name a recipe the ancestor walk cannot reach (a sibling corpus)",
            ),
        ),
    ),
)
@click.argument("path", type=click.Path())
@click.option(
    "--stop-at",
    default=None,
    type=click.Path(),
    help=(
        "Bound the upward search for a recipe at this directory (inclusive). "
        "Without it one spec at the top of a shared filesystem would declare "
        "every tree beneath it rebuildable."
    ),
)
@click.option(
    "--spec",
    "specs",
    multiple=True,
    type=click.Path(),
    help=(
        "Name a recipe the ancestor walk cannot reach (repeatable). Each is "
        "VERIFIED to exist before it counts -- naming a path is not evidence "
        "it is there. The first EXISTING one wins, so a stale entry cannot "
        "shadow a real one behind it, and a DISCOVERED recipe still beats a "
        "supplied one."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.pass_context
def regenerable_cmd(ctx, path, stop_at, specs, as_json):
    verdict = is_regenerable(
        path, stop_at=stop_at, extra_spec_paths=list(specs) or None
    )
    payload = verdict_to_dict(verdict)

    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(f"{verdict.verdict}: {verdict.path}")
        if verdict.spec_path:
            click.echo(f"  recipe: {verdict.spec_path} ({payload['spec_provenance']})")
        if verdict.reason:
            click.echo(f"  reason: {verdict.reason}")
        click.echo(f"  {verdict.evidence}")

    ctx.exit(EXIT_CODES[verdict.verdict])

# EOF
