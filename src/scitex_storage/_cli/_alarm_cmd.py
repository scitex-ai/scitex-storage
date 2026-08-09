#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-storage alarm`` -- check storage and TELL SOMEONE, on a schedule.

The sibling verb ``fleet-status`` renders a dashboard. This one is the
half that does not wait to be looked at: it gathers, decides, and pushes
when the level TRANSITIONS. Run it from a timer; it is the intended
periodic entry point.

WHY THIS VERB EXISTS AT ALL (measured, 2026-08-09): scitex-compute-04
reached 364 MB free on a 393 GB volume and nothing reported it. The
dashboard would have shown it in red, correctly, to nobody -- it surfaced
only because an unrelated five-minute cron on another agent happened to
write and died with ENOSPC. A board is for a reader who is already
looking; an alarm is for one who is not.

STATE LIVES HERE, NOT IN THE RULE. :func:`._alarm.should_notify` is pure
and takes the previous level as an argument, so the CLI owns the file that
remembers it. That keeps the decision exhaustively testable without a
filesystem, and it is why this module is the only place that touches disk.

A LOST STATE FILE MUST NOT READ AS "NOTHING CHANGED". If the file is
missing or unparseable the previous level is ``None``, which
:func:`should_notify` treats as "speak if there is anything to say" --
never as silence. A monitor whose amnesia is indistinguishable from calm
is a gate that cannot fail.

EXIT CODES, declared rather than improvised (see ``_lazy.py`` on why 1/2
are never overloaded with a domain meaning):

  0   the check ran. This is success EVEN WHEN THE FLEET IS ON FIRE --
      the verb's job is to check and report, and conflating "storage is
      unhealthy" with "the monitor failed" makes a timer's failure mail
      useless for both.
  30  the check ran, a push was required, and the transport REFUSED it.
      Distinct because an alarm that could not be delivered is a
      monitoring outage, not a storage condition, and the two need
      different humans.

Note that an UNCONFIRMABLE delivery is NOT 30. The default rail writes to
the card store, which proves the message was stored, not read; reporting
that as either success or failure would be a claim the transport cannot
support.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import click

from .._alarm import evaluate_snapshot, format_alarm
from .._alarm_notify import notify_if_needed, operator_dm_notifier
from .._fleet_status import gather_fleet_snapshot
from ._compat import spec_command_kwargs

#: Exit code for "alarm needed, transport refused". See the module docstring.
EXIT_PUSH_REFUSED = 30


def _default_state_path() -> Path:
    """Where the previous alarm level is remembered.

    Resolved fresh on every call rather than as a module constant so a test
    can sandbox it via ``$HOME`` -- the same reason
    ``_fleet_status_cmd._default_output`` does it that way.
    """
    return Path("~/.scitex/scitex-storage/runtime/alarm-state.json").expanduser()


def read_previous_level(path: Path) -> str | None:
    """The level recorded by the last run, or ``None`` if unknown.

    Every failure mode -- absent file, unreadable file, malformed JSON,
    missing key -- collapses to ``None``, and that is deliberate: they all
    mean "we do not know what we last said". They must NOT mean "the same
    as now", which is what returning the current level would imply and
    which would silence a fresh process standing in front of a full disk.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    level = data.get("level") if isinstance(data, dict) else None
    return level if isinstance(level, str) else None


def write_level(path: Path, level: str, generated_at: str) -> None:
    """Record the level for the next run. Atomic: a reader never sees half.

    Temp + rename, matching ``_observe._atomic_write``. A half-written
    state file would be unparseable, which reads as ``None``, which makes
    the next run re-announce an alarm the reader already has -- recoverable,
    but noisy, and avoidable for the cost of a rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"level": level, "generated_at": generated_at}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


@click.command(
    "alarm",
    **spec_command_kwargs(
        summary="Check fleet storage and PUSH an alarm when the level changes.",
        description=(
            "The half a dashboard cannot do: gathers the fleet, decides "
            "whether anything is alarming, and notifies a human when the "
            "level TRANSITIONS -- escalation always, recovery exactly once, "
            "an unchanged level never. Thresholds are both percentage (85%, "
            "shared with the dashboard so the two cannot disagree) and "
            "absolute free space (20 GiB warn / 5 GiB critical), because a "
            "percentage cannot answer how long you have got. Inodes get the "
            "same treatment as bytes: inode exhaustion fails writes while df "
            "still shows free space. A filesystem that could not be measured "
            "reports UNKNOWN, never OK, and does not page on its own. "
            "Intended to be run from a timer; --dry-run prints what would be "
            "sent and touches no state."
        ),
        examples=(
            ("{prog} alarm", "check and push if the level changed"),
            ("{prog} alarm --dry-run", "print what would be sent; change nothing"),
            ("{prog} alarm --json", "machine-readable result for a timer's logs"),
        ),
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be sent and do NOT push or record state.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON.")
@click.option(
    "--state",
    "state",
    type=click.Path(dir_okay=False),
    default=None,
    help="Where to remember the previous level (default: the runtime tree).",
)
def alarm_cmd(dry_run: bool, as_json: bool, state: str | None) -> None:
    snapshot = gather_fleet_snapshot()
    alarm = evaluate_snapshot(snapshot)
    state_path = Path(state).expanduser() if state else _default_state_path()
    previous = read_previous_level(state_path)

    if dry_run:
        # A dry run must not record state: recording it would make the NEXT
        # real run believe the reader had already been told, which is how a
        # rehearsal silences the performance.
        result = notify_if_needed(alarm, lambda text: None, previous_level=previous)
        payload = {
            "dry_run": True,
            "previous_level": previous,
            **asdict(result),
            "message": format_alarm(alarm),
        }
        click.echo(json.dumps(payload, indent=2) if as_json else _human(payload))
        return

    result = notify_if_needed(alarm, operator_dm_notifier(), previous_level=previous)
    write_level(state_path, alarm.level, snapshot.generated_at)

    payload = {
        "dry_run": False,
        "previous_level": previous,
        **asdict(result),
        "message": format_alarm(alarm),
    }
    click.echo(json.dumps(payload, indent=2) if as_json else _human(payload))
    if result.is_failure:
        raise SystemExit(EXIT_PUSH_REFUSED)


def _human(payload: dict) -> str:
    """Plain-text rendering. States what was decided AND why, in that order."""
    lines = [payload["message"]]
    if payload["attempted"]:
        lines.append(f"pushed: {payload['detail']}")
    else:
        lines.append(f"not pushed: {payload['detail']}")
    return "\n".join(lines)

# EOF
