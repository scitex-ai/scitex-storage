#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage validate-inodes`` — inode capacity of the mount(s) backing PATH(s).

EXIT CODES are the point of this command, not an afterthought. This verb
exists to be run unattended (cron, a CI step, a job prologue), and an
unattended caller reads the exit code, not the table. So the three-state
verdict from :mod:`scitex_storage._inodes` is carried all the way out:

* ``0`` — every path MEASURED and under the threshold. Genuinely fine.
* ``1`` — at least one path MEASURED and at/over the threshold. Act.
* ``2`` — at least one path COULD NOT BE LOOKED AT, and nothing was over
  the threshold.

2 is separate from both 0 and 1 deliberately. Folding "could not look"
into 0 would let a monitoring cron report healthy for a filesystem it
never read — the exact failure that put five live CI runners on a delete
list elsewhere in this fleet this week (an HTTP 404 folded into "absent").
Folding it into 1 would be safer but cries wolf, and an alarm that cries
wolf gets muted, which lands you back at 0 with extra steps. A caller
that wants to treat blindness as failure can `[ $? -ne 0 ]`; a caller that
wants to distinguish can. Both are one line, and neither is the default
that hides the problem.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from .._inodes import (
    COULD_NOT_LOOK,
    DEFAULT_WARN_PERCENT,
    MEASURED,
    NOT_APPLICABLE,
    InodeUsage,
    probe_paths,
)
from ._compat import spec_command_kwargs


def _fmt_count(num: int | None) -> str:
    return "-" if num is None else f"{num:,}"


def _usage_dict(u: InodeUsage) -> dict:
    return {
        "path": str(u.path),
        "verdict": u.verdict,
        "mount": u.mount,
        "fstype": u.fstype,
        "total": u.total,
        "used": u.used,
        "free": u.free,
        "percent_used": (None if u.percent_used is None else round(u.percent_used, 2)),
        "detail": u.detail,
    }


def _format_report(usages: list[InodeUsage], warn_percent: float) -> str:
    lines: list[str] = []
    header = "scitex-storage validate-inodes"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"  {'USED%':>6}  {'USED':>12}  {'TOTAL':>12}  {'MOUNT':<20}  PATH"
    )
    lines.append(f"  {'-' * 6}  {'-' * 12}  {'-' * 12}  {'-' * 20}  {'-' * 20}")

    for u in usages:
        if u.verdict == MEASURED:
            pct = f"{u.percent_used:.1f}" if u.percent_used is not None else "-"
            flag = "  <-- CRITICAL" if u.exceeds(warn_percent) else ""
        else:
            pct = "?"
            flag = f"  <-- {u.verdict.upper()}"
        lines.append(
            f"  {pct:>6}  {_fmt_count(u.used):>12}  {_fmt_count(u.total):>12}  "
            f"{(u.mount or '-'):<20}  {u.path}{flag}"
        )

    notes = [u for u in usages if u.detail]
    if notes:
        lines.append("")
        for u in notes:
            lines.append(f"  {u.path}: {u.detail}")

    # State plainly what the numbers are ABOUT. A figure attributed to the
    # wrong thing is a dangerous near-miss, so the mount is named in-band
    # rather than left to the docstring.
    lines.append("")
    lines.append(
        "  Figures are for the MOUNT named above, whatever that mount "
        "reports: a plain filesystem reports its own inode table, while a "
        "GPFS project fileset reports that project's quota."
    )
    return "\n".join(lines)


@click.command(
    "validate-inodes",
    **spec_command_kwargs(
        summary="Report inode (file-count) capacity for the mount(s) backing PATH(s).",
        description=(
            "Inode exhaustion fails every write while df still shows free "
            "space, and the jobs it kills rarely say why -- so this is a "
            "cheap, dependency-free probe meant to run unattended and early. "
            "It is O(1) per path (one statvfs, no directory walk), needs no "
            "system binaries, and needs no login shell, so it works from a "
            "bare job step or a container where richer tools do not. "
            "Verdicts are three-state and never conflated: a filesystem that "
            "could not be read reports could-not-look, and one with no fixed "
            "inode table (btrfs/ZFS) reports not-applicable -- neither is "
            "ever rendered as a reassuring 0%. Exit code is 0 when all paths "
            "are measured and under the threshold, 1 when any is at/over it, "
            "and 2 when any could not be looked at.",
        ),
        examples=(
            ("{prog} validate-inodes", "check the current directory's filesystem"),
            ("{prog} validate-inodes / /home --json", "machine-readable, several paths"),
            ("{prog} validate-inodes /data --warn-at 80", "alarm earlier on a busy tree"),
            ("{prog} validate-inodes /data || echo ACT", "use the exit code from cron"),
        ),
    ),
)
@click.argument("paths", nargs=-1, type=click.Path())
@click.option(
    "--warn-at",
    "warn_percent",
    type=float,
    default=DEFAULT_WARN_PERCENT,
    show_default=True,
    help="Percent used at/over which a path is CRITICAL (exit 1).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.pass_context
def inodes_cmd(
    ctx: click.Context,
    paths: tuple[str, ...],
    warn_percent: float,
    as_json: bool,
) -> None:
    targets: list[str | Path] = list(paths) if paths else [Path.cwd()]
    usages = probe_paths(targets)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "warn_percent": warn_percent,
                    "results": [_usage_dict(u) for u in usages],
                },
                indent=2,
            )
        )
    else:
        click.echo(_format_report(usages, warn_percent))

    if any(u.exceeds(warn_percent) for u in usages):
        ctx.exit(1)
    if any(u.verdict == COULD_NOT_LOOK for u in usages):
        ctx.exit(2)
    # NOT_APPLICABLE is exit 0: "this filesystem cannot run out of inodes"
    # is a real, complete answer -- not a failure to answer.
    assert all(u.verdict in (MEASURED, NOT_APPLICABLE) for u in usages)
    ctx.exit(0)


# EOF
