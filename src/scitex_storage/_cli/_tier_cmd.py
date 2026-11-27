#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage tier`` — move cold units of a hot volume to a colder host."""

from __future__ import annotations

import json
import re
import shutil
import sys
import time

import click

from .._report import format_size
from .._transfer._archive import DESTINATIONS
from .._transfer._archive_transport import (
    RETIRED_DESTINATIONS,
    probe_transport,
    resolve_destination,
)
from .._transfer._tier import (
    DEFAULT_COLD_AFTER_DAYS,
    DEFAULT_MIN_SIZE_BYTES,
    TierRefusedError,
    apply_tier,
    plan_tier,
)
from ._compat import spec_command_kwargs

#: Some unit failed (its source is intact or staged, and it has no pointer).
#: Outside every other verb's range: 10-19 are verdicts, 20 is verb-unavailable.
EXIT_TIER_FAILURES = 21

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:I?B)?\s*$", re.IGNORECASE)
_POWERS = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5}


class SizeType(click.ParamType):
    """A byte size: ``500M``, ``1.5G``, ``2TiB`` or plain bytes (binary units)."""

    name = "size"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        match = _SIZE.match(str(value))
        if not match:
            self.fail(f"{value!r} is not a size like 500M, 1.5G or 2TiB", param, ctx)
        number, unit = match.groups()
        return int(float(number) * (1 << (10 * _POWERS[unit.upper()])))


def _plan_text(plan, applied: bool) -> list[str]:
    head = (
        f"scitex-storage tier  {plan.root} -> {plan.destination}:{plan.remote_root}"
        + ("" if applied else "  (DRY-RUN)")
    )
    lines = [head, "=" * len(head)]
    target = (
        f", target {plan.target_used_percent:.1f}% used"
        if plan.target_used_percent is not None
        else ""
    )
    lines.append(
        f"cold after {plan.cold_after_days:g} d, min size "
        f"{format_size(plan.min_size_bytes)}{target}"
    )
    if plan.used_percent is not None:
        lines.append(
            f"filesystem: {format_size(plan.used_bytes)} used of "
            f"{format_size(plan.total_bytes)} ({plan.used_percent:.1f}%) -> "
            f"projected {plan.projected_used_percent:.1f}% once staging is swept"
        )
    lines.append("")
    if plan.units:
        lines.append(f"{'AGE(d)':>7}  {'SIZE':>9}  {'KIND':<9}  PATH")
        for unit in plan.units:
            lines.append(
                f"{unit.age_days:>7.0f}  {format_size(unit.size_bytes):>9}  "
                f"{unit.kind:<9}  {unit.path}"
            )
    lines.append(
        f"{len(plan.units)} unit(s), {format_size(plan.selected_bytes)} selected; "
        f"{len(plan.skipped)} entr{'y' if len(plan.skipped) == 1 else 'ies'} skipped"
    )
    reasons: dict[str, int] = {}
    for item in plan.skipped:
        key = item["reason"].split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])[:6]:
        lines.append(f"  skipped {count:>5}  {reason}")
    for warning in plan.warnings:
        lines.append(f"WARN: {warning}")
    return lines


def _run_text(log: dict) -> list[str]:
    lines = [""]
    for entry in log["units"]:
        detail = (
            entry.get("reason") or entry.get("error") or entry.get("pointer_path", "")
        )
        lines.append(f"  {entry['status'].upper():<8} {entry['path']}  {detail}")
    lines.append(
        f"run {log['run_id']}: {log['counts']} -- log {log['log_path']}\n"
        "Originals stay in restorable staging; free the space later with: "
        "scitex-storage sweep-restorable --verify-remote --yes"
    )
    return lines


@click.command(
    "tier",
    **spec_command_kwargs(
        summary="Move cold files and subtrees of ROOT to a colder host, leaving pointers.",
        description=(
            "Selects units under ROOT (a regular file, or the largest directory "
            "subtree that can move whole) whose newest atime and mtime are older "
            "than --cold-after-days and whose size is at least --min-size. The "
            "walk never crosses filesystems or follows symlinks, and skips "
            "restorable staging, existing pointers, --exclude matches and "
            "anything held open. Coldest first, then largest; with "
            "--target-used-percent it stops once the projected usage of ROOT's "
            "filesystem reaches the target. Each unit moves through `archive` "
            "(copy, independent SHA256 read-back, manifest, seven-day restorable "
            "staging) and only then gets a pointer <path>.stx-tiered.json. "
            "`restore <path>` brings it back and removes the pointer after a "
            "verified restore. Each run writes a log under "
            "~/.scitex/scitex-storage/runtime/tier-logs/. Defaults to a dry-run.",
        ),
        examples=(
            ("{prog} tier /volume1 --to scitex-nas-02", "dry-run: list the plan"),
            (
                "{prog} tier /volume1 --to scitex-nas-02 --target-used-percent 80 --yes",
                "tier coldest-first until 80% used is projected",
            ),
        ),
    ),
)
@click.argument("root", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--to",
    "destination",
    type=click.Choice(tuple(DESTINATIONS) + tuple(RETIRED_DESTINATIONS)),
    required=True,
    help="Colder host alias to move units to.",
)
@click.option(
    "--remote-root",
    default=None,
    help="Remote root; each unit mirrors its absolute path under it. "
    "Default: ~/scitex-storage-archive.",
)
@click.option(
    "--cold-after-days",
    type=click.FloatRange(min=0),
    default=DEFAULT_COLD_AFTER_DAYS,
    show_default=True,
    help="A unit is cold when nothing in it was read or written for this long.",
)
@click.option(
    "--min-size",
    "min_size",
    type=SizeType(),
    default=DEFAULT_MIN_SIZE_BYTES,
    show_default="100M",
    help="Smallest unit worth moving (binary units: 500M, 1.5G, 2TiB).",
)
@click.option(
    "--exclude",
    "exclude_patterns",
    multiple=True,
    metavar="PATTERN",
    help="rsync-style pattern relative to ROOT that never moves (repeatable).",
)
@click.option(
    "--target-used-percent",
    type=click.FloatRange(min=0, max=100),
    default=None,
    help="Stop selecting once ROOT's filesystem is projected at or below this.",
)
@click.option("--max-bytes", type=SizeType(), default=None, help="Cap bytes per run.")
@click.option(
    "--max-items", type=click.IntRange(min=1), default=None, help="Cap units per run."
)
@click.option(
    "--dry-run", is_flag=True, help="Preview only (the default without --yes)."
)
@click.option(
    "--yes",
    "-y",
    "confirmed",
    is_flag=True,
    help="Move the units. Without this flag, tier only prints the plan.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
def tier_cmd(
    root: str,
    destination: str,
    remote_root: str | None,
    cold_after_days: float,
    min_size: int,
    exclude_patterns: tuple[str, ...],
    target_used_percent: float | None,
    max_bytes: int | None,
    max_items: int | None,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
) -> None:
    destination, notice = resolve_destination(destination)
    if notice and not as_json:
        click.echo(f"NOTE: {notice}", err=True)
    do_apply = confirmed and not dry_run
    plan = plan_tier(
        root,
        destination=destination,
        remote_root=remote_root,
        cold_after_days=cold_after_days,
        min_size_bytes=min_size,
        exclude=exclude_patterns,
        target_used_percent=target_used_percent,
        max_bytes=max_bytes,
        max_items=max_items,
    )
    # Same contract as `archive`: a plan with work in it must not read as a
    # promise when rsync or the transport is missing. --yes refuses; the
    # dry-run reports. An empty plan needs no transport at all.
    transport = None
    if plan.units:
        if shutil.which("rsync") is None:
            transport = {
                "verdict": "could-not-look",
                "detail": "local rsync not on PATH",
            }
        else:
            probe = probe_transport(destination)
            transport = {"verdict": probe.verdict, "detail": probe.detail}
    blocked = transport is not None and transport["verdict"] != "reachable"
    if do_apply and blocked:
        raise click.ClickException(
            f"transport to {destination!r} is {transport['verdict']}: "
            f"{transport['detail']}\nNothing was moved."
        )
    log = None
    if do_apply:
        try:
            log = apply_tier(plan)
        except TierRefusedError as exc:
            raise click.ClickException(str(exc)) from exc
    if as_json:
        payload = {
            "applied": do_apply,
            "generated_at": time.time(),
            "plan": plan.to_dict(),
            "transport": transport,
            "run": log,
        }
        if notice:
            payload["destination_notice"] = notice
        click.echo(json.dumps(payload, indent=2))
    else:
        lines = _plan_text(plan, applied=do_apply)
        if blocked:
            lines.append(
                f"TRANSPORT {transport['verdict'].upper()}: {transport['detail']}"
            )
        if log is not None:
            lines += _run_text(log)
        elif plan.units:
            lines.append("Re-run with --yes to tier these units.")
        click.echo("\n".join(lines))
    if log is not None and log["counts"]["failed"]:
        sys.exit(EXIT_TIER_FAILURES)


# EOF
