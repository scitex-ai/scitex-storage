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

  30  is also the exit of ``--check-notify`` when a selected rail fails its
      no-send validation: the monitoring rail is broken, storage may be fine.

WATCHING A SPECIFIC MOUNT (#181). ``--path`` (repeatable) replaces the
default ``~`` and ``/`` -- the default could not see scitex-nas-03's
``/volume1`` at all. ``--warn-free-gib`` / ``--crit-free-gib`` set the
absolute floors; on volumes >= :data:`LARGE_VOLUME_BYTES` those floors, not
the 85% rule, decide. Each distinct ``--path`` set keeps its OWN state file,
so a ``/volume1`` alarm and a default alarm on one host cannot silence each
other.

RAILS. ``--notify cards`` (the default, unchanged), ``email``, or
``cards,email`` -- an ordered chain where the email rail (the scoped
operator helper, outside the fleet database) carries the alarm when the
card store is unreachable, and the fallback is named in the output.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

import click

from .._alarm import (
    UNKNOWN,
    Rail,
    Thresholds,
    check_email_rail,
    email_command_notifier,
    evaluate_snapshot,
    format_alarm,
    notify_if_needed,
    operator_dm_notifier,
)
from .._fleet_status import gather_fleet_snapshot
from ._compat import spec_command_kwargs

#: Exit code for "alarm needed, transport refused". See the module docstring.
EXIT_PUSH_REFUSED = 30

#: Rail names ``--notify`` accepts, in no particular order.
RAIL_NAMES = ("cards", "email")


def _default_state_path(paths: tuple[str, ...] | list[str] | None = None) -> Path:
    """Where the previous alarm level is remembered.

    Resolved fresh on every call rather than as a module constant so a test
    can sandbox it via ``$HOME`` -- the same reason
    ``_fleet_status_cmd._default_output`` does it that way.

    With explicit ``paths`` the file name carries a digest of the sorted
    path set. Sharing one file between two differently-scoped alarms would
    let each overwrite the other's level, and then a transition seen by one
    reads as "unchanged" to the other -- a silenced alarm.
    """
    root = Path("~/.scitex/scitex-storage/runtime").expanduser()
    if not paths:
        return root / "alarm-state.json"
    key = "\n".join(sorted(os.path.abspath(os.path.expanduser(p)) for p in paths))
    return root / f"alarm-state-{hashlib.sha256(key.encode()).hexdigest()[:12]}.json"


def parse_rails(spec: str) -> list[str]:
    """``"cards,email"`` -> ``["cards", "email"]``; unknown or empty refused.

    Order is preserved: it IS the fallback order.
    """
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if not names:
        raise click.BadParameter("at least one rail is required", param_hint="--notify")
    bad = [n for n in names if n not in RAIL_NAMES]
    if bad:
        raise click.BadParameter(
            f"unknown rail(s) {bad}; choose from {list(RAIL_NAMES)}",
            param_hint="--notify",
        )
    if len(set(names)) != len(names):
        raise click.BadParameter("a rail is listed twice", param_hint="--notify")
    return names


def build_rails(names: list[str], email_command: str | None = None) -> list[Rail]:
    """Turn rail names into live :class:`Rail` objects, in order."""
    factories = {
        "cards": lambda: operator_dm_notifier(),
        "email": lambda: email_command_notifier(email_command),
    }
    return [Rail(n, factories[n]()) for n in names]


def check_rails(names: list[str], email_command: str | None = None) -> dict:
    """Validate each selected rail WITHOUT delivering anything.

    cards: the client imports (it cannot be probed without writing a DM, so
    this is necessary-not-sufficient, and is labelled as such). email: the
    helper's own ``--dry-run``, which reaches everything short of SMTP.
    """
    out: dict = {}
    for n in names:
        if n == "cards":
            try:
                import scitex_cards  # noqa: F401 -- presence check only
            except Exception as exc:  # noqa: BLE001 -- a broken install is a failed check
                out[n] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            else:
                out[n] = {
                    "ok": True,
                    "detail": "scitex_cards importable (store reachability not probed: "
                    "probing would send a DM)",
                }
        elif n == "email":
            out[n] = check_email_rail(email_command)
    return out


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


def read_unknown_streak(path: Path) -> int:
    """Consecutive UNKNOWN gathers recorded so far, or 0 if unknown.

    Zero on every failure mode, matching :func:`read_previous_level`: a lost
    counter restarts the count rather than inventing history. That delays a
    sustained-blindness page by at most the streak length; claiming a streak
    we cannot substantiate would be worse than being slightly late.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    streak = data.get("unknown_streak") if isinstance(data, dict) else None
    return streak if isinstance(streak, int) and streak >= 0 else 0


def next_unknown_streak(previous_streak: int, current_level: str) -> int:
    """Advance the blindness counter. Pure, so the caller can test it.

    Counts consecutive UNKNOWN gathers INCLUDING the current one, and resets
    to zero the moment anything is measured. The reset is the important half:
    a streak that survived a successful gather would eventually fire a
    blindness page about a filesystem we can currently read.
    """
    return previous_streak + 1 if current_level == UNKNOWN else 0


def write_level(
    path: Path, level: str, generated_at: str, unknown_streak: int = 0
) -> None:
    """Record the level for the next run. Atomic: a reader never sees half.

    Temp + rename, matching ``_observe._atomic_write``. A half-written
    state file would be unparseable, which reads as ``None``, which makes
    the next run re-announce an alarm the reader already has -- recoverable,
    but noisy, and avoidable for the cost of a rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(
            {
                "level": level,
                "generated_at": generated_at,
                "unknown_streak": unknown_streak,
            },
            indent=2,
        ),
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
            "sent and touches no state. --path watches specific mounts (e.g. "
            "a NAS volume); --warn-free-gib/--crit-free-gib set the floors, "
            "which alone decide on volumes of 1 TiB or more. --notify "
            "cards,email falls back to email when the card store (the fleet "
            "database) is unreachable, and says so."
        ),
        examples=(
            ("{prog} alarm", "check and push if the level changed"),
            ("{prog} alarm --dry-run", "print what would be sent; change nothing"),
            ("{prog} alarm --json", "machine-readable result for a timer's logs"),
            (
                "{prog} alarm --path /volume1 --warn-free-gib 100 --crit-free-gib 30 "
                "--notify cards,email",
                "watch one large mount, email if cards is down",
            ),
            (
                "{prog} alarm --notify cards,email --check-notify",
                "validate rails; send nothing",
            ),
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
    help="Where to remember the previous level (default: the runtime tree, "
    "one file per --path set).",
)
@click.option(
    "--path",
    "paths",
    multiple=True,
    help="Filesystem to check (repeatable). Default: your home and /.",
)
@click.option(
    "--warn-free-gib",
    type=click.FloatRange(min=0),
    default=None,
    help="WARN at/under this many GiB free (default 20).",
)
@click.option(
    "--crit-free-gib",
    "--critical-free-gib",
    "crit_free_gib",
    type=click.FloatRange(min=0),
    default=None,
    help="CRITICAL at/under this many GiB free (default 5).",
)
@click.option(
    "--notify",
    "notify_spec",
    default="cards",
    show_default=True,
    help="Rail(s), comma-separated, tried in order: cards, email.",
)
@click.option(
    "--email-command",
    default=None,
    help="Email helper command (default: $SCITEX_STORAGE_ALARM_EMAIL_COMMAND, "
    "else the ywatanabe-email-notify skill helper).",
)
@click.option(
    "--check-notify",
    is_flag=True,
    help="Validate the selected rails WITHOUT sending, then exit (30 if a rail fails).",
)
def alarm_cmd(
    dry_run: bool,
    as_json: bool,
    state: str | None,
    paths: tuple[str, ...],
    warn_free_gib: float | None,
    crit_free_gib: float | None,
    notify_spec: str,
    email_command: str | None,
    check_notify: bool,
) -> None:
    rail_names = parse_rails(notify_spec)
    try:
        thresholds = Thresholds.from_gib(warn_free_gib, crit_free_gib)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--crit-free-gib") from exc

    if check_notify:
        checks = check_rails(rail_names, email_command)
        click.echo(
            json.dumps(checks, indent=2)
            if as_json
            else "\n".join(
                f"{n}: {'ok' if c['ok'] else 'FAILED'} -- "
                + (
                    c.get("detail")
                    or f"rc={c.get('rc')} status={c.get('status')} "
                    f"{c.get('stderr', '')}".strip()
                )
                for n, c in checks.items()
            )
        )
        if not all(c["ok"] for c in checks.values()):
            raise SystemExit(EXIT_PUSH_REFUSED)
        return

    snapshot = gather_fleet_snapshot(list(paths) or None)
    alarm = evaluate_snapshot(snapshot, thresholds)
    state_path = Path(state).expanduser() if state else _default_state_path(paths)
    previous = read_previous_level(state_path)
    streak = next_unknown_streak(read_unknown_streak(state_path), alarm.level)
    readings = _readings(alarm)

    if dry_run:
        # A dry run must not record state: recording it would make the NEXT
        # real run believe the reader had already been told, which is how a
        # rehearsal silences the performance.
        result = notify_if_needed(
            alarm, lambda text: None, previous_level=previous, unknown_streak=streak
        )
        payload = {
            "dry_run": True,
            "previous_level": previous,
            "unknown_streak": streak,
            "rails": rail_names,
            "state_path": str(state_path),
            **asdict(result),
            "message": format_alarm(alarm),
            "filesystems": readings,
        }
        click.echo(json.dumps(payload, indent=2) if as_json else _human(payload))
        return

    result = notify_if_needed(
        alarm,
        build_rails(rail_names, email_command),
        previous_level=previous,
        unknown_streak=streak,
    )
    write_level(state_path, alarm.level, snapshot.generated_at, streak)

    payload = {
        "dry_run": False,
        "previous_level": previous,
        "unknown_streak": streak,
        "rails": rail_names,
        "state_path": str(state_path),
        **asdict(result),
        "message": format_alarm(alarm),
        "filesystems": readings,
    }
    click.echo(json.dumps(payload, indent=2) if as_json else _human(payload))
    if result.is_failure:
        raise SystemExit(EXIT_PUSH_REFUSED)


def _readings(alarm) -> list[dict]:
    """Per-filesystem numbers, so a quiet run still shows what it measured."""
    return [
        {
            "host": fs.host,
            "mount": fs.mount,
            "level": fs.level,
            "free_gib": None
            if fs.free_bytes is None
            else round(fs.free_bytes / 1024**3, 1),
            "used_pct": fs.used_pct,
            "inode_used_pct": fs.inode_used_pct,
            "reasons": fs.reasons,
        }
        for fs in alarm.filesystems
    ]


def _human(payload: dict) -> str:
    """Plain-text rendering. States what was decided AND why, in that order."""
    lines = [payload["message"]]
    for r in payload.get("filesystems", []):
        free = "?" if r["free_gib"] is None else f"{r['free_gib']} GiB free"
        lines.append(
            f"  [{r['level']}] {r['host']}:{r['mount']} -- "
            f"{free}, {r['used_pct']}% used"
        )
    if payload["attempted"]:
        lines.append(f"pushed: {payload['detail']}")
    else:
        lines.append(f"not pushed: {payload['detail']}")
    return "\n".join(lines)


# EOF
