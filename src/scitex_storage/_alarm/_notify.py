#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_alarm/_notify.py
"""Deliver a storage alarm to a human, and report honestly whether it landed.

:mod:`._alarm` decides WHETHER to alarm and renders WHAT to say. This
module is the part that leaves the process, kept separate for the same
reason ``_observe/_df.py`` is separate from its transport: the decision
layer is pure and exhaustively testable, and everything that can fail for
environmental reasons lives on this side of the line.

DISPATCH IS NOT DELIVERY. The whole point of the incident behind this
module is that something was true and nobody was told, so a notifier that
returns "sent" without evidence would reproduce the defect one layer up.
:class:`PushResult` therefore carries ``delivered`` as THREE-VALUED --
``True`` (the transport confirmed), ``False`` (the transport refused), and
``None`` (the call completed but confirmation is not available). Collapsing
``None`` into ``True`` is how a dead channel reports success forever; the
fleet has a measured instance of exactly that, where a peer's
``delivered_subscriber_count: 1`` attested handover to a live subscriber
rather than durable delivery.

THE TRANSPORT IS INJECTED, not imported at the top. A ``notifier`` is any
callable taking the rendered text and returning ``bool | None`` with the
meaning above, which lets the tests drive real code paths with a real
function -- data, not a mock -- and lets a caller in a different
environment (a cron on a bare host, a container without the card store)
supply its own rail without this module knowing about it.

FALLBACK IS ALLOWED ONLY WHEN IT IS LOUD. This module used to say "one
rail, no fallback chain", for a sound reason: a fallback that quietly
succeeds on rail two hides that rail one is broken, and the next incident
finds every rail rotten. 2026-11-25 showed the cost of the other side of
that trade. ``/volume1`` on scitex-nas-03 filled and stopped the fleet
Postgres -- the very database the only rail (``scitex_cards.dm_send``)
writes to. The alarm for "the disk under the database is full" could only
be delivered THROUGH that database (#181).

So a caller may now pass an ordered list of :class:`Rail`, and what keeps
it honest is that a fallback is never silent: every rail that failed is
named with its error in :attr:`PushResult.detail` and listed in
:attr:`PushResult.rail_errors`, and :attr:`PushResult.rail` says which one
carried the message. A push that succeeded on the fallback therefore still
reads as "your primary rail is broken", which is the half the old rule was
protecting. The email rail (:func:`email_command_notifier`) runs outside
the monitored database entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence, Union

from ._decide import (
    UNKNOWN,
    FleetAlarm,
    format_alarm,
    format_blindness,
    should_notify,
)
from ._repeat import PushDecision, format_changes

#: A transport: takes the rendered alarm text, returns whether it landed.
#: ``None`` means "completed, but this rail cannot confirm delivery".
Notifier = Callable[[str], Optional[bool]]

#: How long one rail may take before it counts as failed and the next rail
#: is tried. A database that is down can hang a connect for minutes; the
#: alarm about that database must not wait on it.
RAIL_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class Rail:
    """A named transport. The name is what a failure report says."""

    name: str
    notifier: Notifier


def _call_bounded(notifier: Notifier, text: str, timeout_s: float) -> Optional[bool]:
    """Run ``notifier(text)``, raising ``TimeoutError`` past ``timeout_s``.

    A daemon thread rather than an executor: an executor joins its workers
    at interpreter exit, so a rail hung on a dead database would still hold
    the process open after we had moved on to the next rail.
    """
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = notifier(text)
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise TimeoutError(f"no answer within {timeout_s:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


@dataclass
class PushResult:
    """What happened when we tried to tell someone. A FIXED shape, always.

    Returned identically whether we pushed, deliberately stayed quiet, or
    failed, so a caller never guesses which fields exist. ``attempted``
    distinguishes "we chose not to speak" from "we spoke and it failed" --
    two states that a bare ``False`` would merge, and only one of which is
    a problem.
    """

    attempted: bool
    delivered: bool | None
    level: str
    text: str = ""
    detail: str = ""
    #: Name of the rail that carried the message ("" when none did).
    rail: str = ""
    #: ``"<rail>: <what went wrong>"`` for every rail that failed first.
    rail_errors: list[str] = field(default_factory=list)

    @property
    def is_failure(self) -> bool:
        """True only for an attempted push the transport actively refused.

        ``delivered=None`` is NOT a failure -- it is an unknown, and
        treating it as either pole is the error this class exists to avoid.
        """
        return self.attempted and self.delivered is False


def notify_if_needed(
    alarm: FleetAlarm,
    notifier: Union[Notifier, Sequence[Rail]],
    previous_level: str | None = None,
    unknown_streak: int = 0,
    rail_timeout_s: float = RAIL_TIMEOUT_S,
    decision: PushDecision | None = None,
) -> PushResult:
    """Push ``alarm`` if the level TRANSITIONED, and report what happened.

    The transition rule lives in :func:`._alarm.should_notify` -- pure and
    tested there -- so this function stays a transport with no policy of
    its own. ``previous_level`` is passed IN rather than read from disk
    here: persistence is the caller's concern, which keeps this testable
    without a filesystem and lets the state live wherever the caller's
    runtime already keeps state.

    A notifier that RAISES is caught and reported as a failure rather than
    propagating. An alarm path that can itself crash the periodic job would
    take down the gather that feeds it -- the monitor becoming the outage
    is a failure mode worth spending a try/except on. The exception type
    and message are preserved in ``detail`` so the failure is diagnosable,
    never merely counted.

    ``notifier`` may be one callable (a single rail named ``"default"``) or
    an ordered sequence of :class:`Rail`. Rails are tried in order until
    one does not fail. A rail returning ``None`` (stored, unconfirmed)
    stops the chain -- it did not fail. Each rail is bounded by
    ``rail_timeout_s``.

    ``decision`` (from :func:`._repeat.decide_filesystems`) adds the
    per-filesystem rule: the alarm is pushed when EITHER the decision says
    so OR the fleet-level sustained-blindness page is due, and the
    decision's change lines are appended so the reader sees which
    filesystem moved. ``previous_level`` is then not consulted for capacity
    transitions -- the per-filesystem memory is the finer, newer record.
    """
    rails = [Rail("default", notifier)] if callable(notifier) else list(notifier)

    if decision is None:
        speak = should_notify(previous_level, alarm.level, unknown_streak)
        why = f"no transition ({previous_level} -> {alarm.level})"
    else:
        blind = alarm.level == UNKNOWN and should_notify(
            UNKNOWN, UNKNOWN, unknown_streak
        )
        speak = decision.send or blind
        why = decision.reason
    if not speak:
        return PushResult(
            attempted=False,
            delivered=None,
            level=alarm.level,
            detail=why,
        )

    # Sustained blindness gets its own wording. Reusing the capacity text
    # would tell the reader a filesystem is healthy-or-not when the true
    # statement is that we have not read it at all.
    text = (
        format_blindness(alarm, unknown_streak)
        if alarm.level == UNKNOWN
        else format_alarm(alarm)
    )
    if decision is not None and decision.changes:
        text = f"{text}\n{format_changes(decision.changes)}"

    errors: list[str] = []
    for rail in rails:
        try:
            delivered = _call_bounded(rail.notifier, text, rail_timeout_s)
        except Exception as exc:  # noqa: BLE001 -- see docstring: never crash the gather
            errors.append(f"{rail.name}: notifier raised {type(exc).__name__}: {exc}")
            continue
        if delivered is False:
            errors.append(f"{rail.name}: transport reported failure")
            continue
        outcome = "delivered" if delivered else "transport could not confirm delivery"
        if errors:
            # LOUD fallback: the broken primary leads the sentence.
            outcome = f"FALLBACK via {rail.name} ({outcome}) after " + "; ".join(errors)
        return PushResult(
            attempted=True,
            delivered=delivered,
            level=alarm.level,
            text=text,
            detail=outcome,
            rail=rail.name,
            rail_errors=errors,
        )

    return PushResult(
        attempted=True,
        delivered=False,
        level=alarm.level,
        text=text,
        detail="; ".join(errors) if errors else "no rail configured",
        rail="",
        rail_errors=errors,
    )


def operator_dm_notifier(to: str = "operator") -> Notifier:
    """A notifier that DMs the operator through the shared card store.

    Returns ``None`` rather than ``True`` on success, and the distinction
    is deliberate: the store accepts the message and hands back a stored
    row, which proves it was WRITTEN, not that anyone READ it. Every status
    DM this agent sent on 2026-08-09 committed successfully and sat
    ``read: false`` for hours. Reporting that as confirmed delivery would
    be precisely the lie this module is built to refuse.

    The import is deferred so scitex-storage does not take a hard
    dependency on the card store just to define this function -- a caller
    on a host without it supplies a different notifier and never touches
    this code path.
    """

    def send(text: str) -> bool | None:
        try:
            from scitex_cards import dm_send  # deferred: optional dependency
        except ImportError as exc:
            raise ImportError(
                "operator_dm_notifier requires scitex-cards: pip install scitex-cards"
            ) from exc

        dm_send(to=to, body=text)
        return None  # written to the store; being read is not established

    return send


#: The fleet's scoped operator-email helper (the ``ywatanabe-email-notify``
#: skill). It holds no secret itself: it hands off to an administrator-
#: installed launcher that decrypts only the agent mailbox, so this package
#: never touches a credential.
#: Run through bash, as the skill documents: the helper ships mode 0600/0644,
#: not executable, and that is deliberate on the hosts that carry it.
DEFAULT_EMAIL_COMMAND = "/bin/bash ~/.claude/skills/ywatanabe-email-notify/scripts/send.sh"

#: Environment override for the email command, for hosts whose helper lives
#: elsewhere. Any command honouring the same contract works:
#: ``--event-id ID --subject S --body-file F [--dry-run]``, printing a JSON
#: line with ``status`` and exiting 0 on SMTP acceptance.
EMAIL_COMMAND_ENV = "SCITEX_STORAGE_ALARM_EMAIL_COMMAND"

#: Helper statuses that mean the SMTP server accepted the message.
_ACCEPTED = frozenset({"smtp_accepted", "already_accepted"})


def resolve_email_command(command: str | None = None) -> list[str]:
    """argv for the email helper: explicit > env > default, ``~`` expanded."""
    raw = command or os.environ.get(EMAIL_COMMAND_ENV) or DEFAULT_EMAIL_COMMAND
    argv = shlex.split(raw)
    if not argv:
        raise ValueError("empty email command")
    argv = [os.path.expanduser(a) if a.startswith("~") else a for a in argv]
    return argv


def _last_json(stdout: str) -> dict:
    """The helper prints one JSON object per run; take the last parseable line."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def run_email_command(
    argv: list[str],
    subject: str,
    body: str,
    event_id: str,
    dry_run: bool = False,
    timeout_s: float = 90.0,
) -> dict:
    """Invoke the helper contract and return ``{"rc", "status", "stderr"}``.

    ``stderr`` is the helper's own diagnostic tail; the helper is written
    never to print a credential and nothing here adds one. A missing or
    non-executable helper is REPORTED (rc 127/126), not raised, so a
    configuration check can describe it.
    """
    subject = " ".join(subject.split())  # the helper refuses multi-line subjects
    fd, body_path = tempfile.mkstemp(suffix=".txt", prefix="scitex-storage-alarm-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        cmd = [
            *argv,
            "--event-id",
            event_id,
            "--subject",
            subject,
            "--body-file",
            body_path,
        ]
        if dry_run:
            cmd.append("--dry-run")
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except FileNotFoundError as exc:
            return {"rc": 127, "status": "helper_missing", "stderr": str(exc)}
        except PermissionError as exc:
            return {"rc": 126, "status": "helper_not_executable", "stderr": str(exc)}
        return {
            "rc": proc.returncode,
            "status": _last_json(proc.stdout).get("status", ""),
            "stderr": proc.stderr.strip()[-500:],
        }
    finally:
        os.unlink(body_path)


def email_event_id(text: str, host: str | None = None) -> str:
    """Stable per message, so a retry of the SAME alarm cannot send twice.

    The alarm text carries ``generated_at``, so two genuine alarms never
    share an id, while a re-delivery attempt of one alarm does.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"scitex-storage-alarm:{host or socket.gethostname()}:{digest}"


def email_command_notifier(command: str | None = None) -> Notifier:
    """A notifier that emails the operator through the scoped helper.

    Independent of the fleet database by construction: SMTP through the
    helper's own launcher, no card store involved. Returns ``True`` only
    when the helper reports SMTP acceptance; anything else RAISES with the
    helper's exit code and status, so the rail chain names it. SMTP
    acceptance is acceptance by the mail server, not proof of reading --
    it is the strongest confirmation this transport can give.
    """

    def send(text: str) -> bool | None:
        argv = resolve_email_command(command)
        first = text.splitlines()[0] if text else "storage alarm"
        result = run_email_command(
            argv,
            subject=f"[SciTeX] scitex-storage alarm: {first}"[:200],
            body=text,
            event_id=email_event_id(text),
        )
        if result["rc"] == 0 and result["status"] in _ACCEPTED:
            return True
        raise RuntimeError(
            f"email helper rc={result['rc']} status={result['status'] or '?'}"
            + (f" ({result['stderr']})" if result["stderr"] else "")
        )

    return send


def check_email_rail(command: str | None = None) -> dict:
    """Validate the email rail WITHOUT sending, via the helper's ``--dry-run``.

    Proves the helper exists, its trusted launcher is installed, and the
    sender/recipient configuration resolves -- everything short of SMTP.
    """
    try:
        argv = resolve_email_command(command)
    except ValueError as exc:
        return {
            "ok": False,
            "rc": 2,
            "status": "bad_command",
            "stderr": str(exc),
            "command": "",
        }
    result = run_email_command(
        argv,
        subject="[SciTeX] scitex-storage alarm rail check (dry run)",
        body="Dry-run validation of the scitex-storage alarm email rail.\n",
        event_id=f"scitex-storage-alarm-check:{socket.gethostname()}",
        dry_run=True,
    )
    result["ok"] = result["rc"] == 0 and result["status"] == "dry_run"
    result["command"] = " ".join(argv)
    return result


# EOF
