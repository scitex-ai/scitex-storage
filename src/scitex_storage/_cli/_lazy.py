#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load each CLI verb only when it is invoked.

WHY THIS EXISTS. `_cli/__init__.py` used to import every verb module at
package import. That couples EVERY verb to EVERY verb's dependencies: one
stale sibling package takes out the whole CLI, including verbs that never
touch it.

MEASURED by scitex-hpc 2026-07-29, inside the real solver image on a Spartan
compute node:

    from ._archive_cmd import archive_cmd, restore_cmd
    from .._archive import ...
    from scitex_ssh import SSHResult, exec_remote, sync_dir
    ImportError: cannot import name 'sync_dir' from 'scitex_ssh'
    exit_code=1   (survey, find-recipe, and everything else -- all of them)

`survey` does not need SSH. `find-recipe` does not need SSH. Both were
unreachable because `archive` does, and the import chain died before argparse
ever saw the subcommand.

TWO SEPARATE DEFECTS, and the second is the one that matters:

  1. Coupling. One dependency skew disables unrelated verbs.
  2. IT EXITED 1. The whole point of this package's exit-code contract is that
     a missing or broken verb must never impersonate a real answer -- 1 and 2
     mean "generic failure" and "usage error" in every CLI framework, so
     domain verdicts start at 10. Here a stale sibling dependency produced a
     bare 1, entering through the one door the design did not cover, because
     the failure was UPSTREAM of the code that owns the contract. A caller
     shelling out could not distinguish "not installed", "could not look",
     and "the import blew up".

So loading lazily is only half the fix. When a verb genuinely cannot load, it
must fail in the SAME declared shape as everything else: a reserved numeric
code, and a message naming the offending module and what to do.
"""

from __future__ import annotations

import importlib
import sys

import click

#: Verb name -> "module:attribute", resolved on first use.
#:
#: Kept as data rather than imports so `list_commands` can answer without
#: importing anything -- help and completion must not be able to fail because
#: some unrelated verb's dependency is skewed.
VERB_REGISTRY: dict[str, str] = {
    "scan": "._scan_cmd:scan_cmd",
    "validate-inodes": "._inodes_cmd:inodes_cmd",
    "find-duplicates": "._duplicates_cmd:find_duplicates_cmd",
    "find-recipe": "._regenerable_cmd:regenerable_cmd",
    "survey": "._survey_cmd:survey_cmd",
    "images": "._images_cmd:images_group",
    "sweep": "._sweep_cmd:sweep_cmd",
    "sweep-status": "._sweep_cmd:sweep_status_cmd",
    "archive": "._archive_cmd:archive_cmd",
    "restore": "._archive_cmd:restore_cmd",
    "reclaim": "._reclaim_cmd:reclaim_cmd",
    "reclaim-restore": "._reclaim_cmd:reclaim_restore_cmd",
    "document-sorter": "._document_sorter_cmd:document_sorter_group",
    "verify-content": "._verify_content_cmd:verify_content_cmd",
    "fleet-status": "._fleet_status_cmd:fleet_status_cmd",
    "alarm": "._alarm_cmd:alarm_cmd",
    # NOTE: `list-python-apis` and `mcp` are deliberately NOT here -- they are
    # attached eagerly in __init__.py. See the comment there.
    "gui": "._gui_cmd:gui_group",
    "start-gui": "._gui_cmd:start_gui_cmd",
}

#: A verb exists but its module could not be imported.
#:
#: DELIBERATELY NOT 1 OR 2 (generic failure / usage error), and deliberately
#: outside every verb's own range -- `find-recipe` owns 10/11 and `survey`
#: owns 12/13. This is a CLI-LEVEL fault, not a verdict about the thing the
#: caller asked about, and conflating the two is exactly what the contract
#: exists to prevent. A caller seeing 20 knows the TOOL is broken, not that
#: the ANSWER is unknown.
EXIT_VERB_UNAVAILABLE = 20


class LazyGroupMixin:
    """Import subcommands on demand.

    A MIXIN rather than a concrete subclass because the base class is not
    ours to choose: `_compat.spec_group_kwargs` supplies scitex-dev's
    `SpecGroup` when its help helpers are installed and nothing when they are
    not, so hard-coding `click.Group` would silently discard the spec-help
    rendering on machines that have it. Mixing over the base that is actually
    in play keeps that rendering intact -- and the first version of this fix
    DID hard-code it, which blew up as
    `group() got multiple values for keyword argument 'cls'`.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        # Answers from the registry WITHOUT importing. `--help` and shell
        # completion therefore keep working even when a verb's dependencies
        # are broken -- which is when a user most needs to be told what
        # exists.
        return sorted(set(VERB_REGISTRY) | set(self.commands))

    def get_command(self, ctx: click.Context, name: str):
        if name in self.commands:  # explicitly attached (e.g. completion)
            return self.commands[name]
        target = VERB_REGISTRY.get(name)
        if target is None:
            return None

        module_name, _, attr = target.partition(":")
        try:
            module = importlib.import_module(module_name, package=__package__)
        except Exception as exc:  # noqa: BLE001 -- report ANY import failure
            return _unavailable_command(name, module_name, exc)
        return getattr(module, attr)


def make_lazy_group(base_cls: type | None) -> type:
    """Build a lazy-loading group class on top of ``base_cls``.

    ``base_cls`` is whatever `spec_group_kwargs` chose (scitex-dev's
    ``SpecGroup``, or ``None`` when its helpers are absent). Returning a
    fresh subclass keeps that choice authoritative instead of overriding it.
    """
    base = base_cls or click.Group
    return type("LazyGroup", (LazyGroupMixin, base), {})


def _unavailable_command(verb: str, module_name: str, exc: Exception):
    """A stand-in command that fails LOUDLY and in the declared shape.

    Returning ``None`` here would make click report "no such command", which
    is a lie: the verb exists and its dependencies are broken. Raising the
    ImportError would exit 1 and reproduce the defect this module fixes. So
    the verb is materialised as a command that explains itself and exits with
    a reserved code.
    """

    @click.command(
        name=verb,
        context_settings={"ignore_unknown_options": True},
        help=f"UNAVAILABLE: {verb} could not be loaded ({type(exc).__name__}).",
    )
    @click.argument("ignored", nargs=-1, type=click.UNPROCESSED)
    def _unavailable(ignored) -> None:  # noqa: ANN001
        click.echo(
            f"scitex-storage: the verb `{verb}` exists but could NOT BE LOADED.\n"
            f"\n"
            f"  cause:  {type(exc).__name__}: {exc}\n"
            f"  module: scitex_storage._cli{module_name}\n"
            f"\n"
            f"This is NOT a verdict about anything you asked for -- the verb "
            f"never ran. It usually means a sibling package on this "
            f"interpreter's path is a different version than this build "
            f"expects (a stale copy inside a container image is the common "
            f"case; the baked package can be older than the one bound over "
            f"it).\n"
            f"\n"
            f"What to do:\n"
            f"  1. python -c 'import <module>; print(<module>.__file__)' for the "
            f"package named in the cause, to see WHICH copy is winning.\n"
            f"  2. Other verbs are unaffected -- `scitex-storage --help` still "
            f"lists everything, and verbs that do not need this dependency "
            f"still work.\n"
            f"\n"
            f"Exit code {EXIT_VERB_UNAVAILABLE} means TOOL BROKEN. It is "
            f"distinct from this CLI's verdict codes so it can never be read "
            f"as an answer.",
            err=True,
        )
        sys.exit(EXIT_VERB_UNAVAILABLE)

    return _unavailable

# EOF
