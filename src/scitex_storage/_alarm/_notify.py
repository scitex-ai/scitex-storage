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
protecting. The email rail (:func:`email_notifier`) runs outside the
monitored database entirely.

OPS ALARMS GO TO THE OPS INBOX (operator, 2026-11-27). The email rail used
to run the operator's personal notification helper, whose sender
(``agent@scitex.ai``) and recipient (``ywatanabe@scitex.ai``) are fixed by
design, so every storage alarm landed in a personal inbox. The rail now
sends as a scitex-notification *profile* (default ``ops``: From
``no-reply@scitex.ai`` To ``dev@scitex.ai``, configured on the alarm host).
An unconfigured profile FAILS the rail by name; it never reroutes to the
personal helper.
"""

from __future__ import annotations

import hashlib
import os
import socket
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


#: The scitex-notification sender/recipient profile the email rail sends
#: as. ``ops`` is the operator's 2026-11-27 routing for system alarms:
#: From ``no-reply@scitex.ai`` To ``dev@scitex.ai``. The addresses are NOT
#: in this package; the alarm host sets them, with the SMTP server and the
#: credential, as ``SCITEX_NOTIFICATION_PROFILE_OPS_{FROM,TO,SMTP_HOST,
#: SMTP_PORT,PASSWORD_FILE}`` (see ``examples/systemd/alarm.env.example``).
DEFAULT_EMAIL_PROFILE = "ops"

#: Environment override for the profile name.
EMAIL_PROFILE_ENV = "SCITEX_STORAGE_ALARM_EMAIL_PROFILE"

#: A re-delivery of the SAME alarm text inside this window is not mailed
#: twice. A rail that timed out after the server accepted the message is
#: retried on the next gather; this keeps that retry from duplicating it.
#: Distinct alarms never collide: the text carries ``generated_at``.
EMAIL_DEDUPE_WINDOW_S = 24 * 3600.0

_NOTIFICATION_HINT = (
    "the email rail needs scitex-notification with sender profiles "
    "(get_profile / send_with_profile): pip install -U scitex-notification"
)


def resolve_email_profile(profile: str | None = None) -> str:
    """Profile name: explicit > ``$SCITEX_STORAGE_ALARM_EMAIL_PROFILE`` > ``ops``."""
    return profile or os.environ.get(EMAIL_PROFILE_ENV) or DEFAULT_EMAIL_PROFILE


def _notification_api():
    """``(get_profile, send_with_profile)``, imported late.

    Deferred so ``scitex_storage`` imports on a host without the package;
    the email rail then RAISES, which the rail chain reports by name. It
    never falls back to another mail path: a silent reroute is how ops
    alarms ended up in the operator's personal inbox.
    """
    try:
        from scitex_notification import get_profile, send_with_profile
    except ImportError as exc:
        raise ImportError(_NOTIFICATION_HINT) from exc
    return get_profile, send_with_profile


def email_event_id(text: str, host: str | None = None) -> str:
    """Stable per message, so a retry of the SAME alarm cannot send twice.

    The alarm text carries ``generated_at``, so two genuine alarms never
    share an id, while a re-delivery attempt of one alarm does.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"scitex-storage-alarm:{host or socket.gethostname()}:{digest}"


def email_subject(text: str, host: str | None = None) -> str:
    """One line naming the host: one inbox receives alarms from many hosts."""
    first = text.splitlines()[0] if text else "storage alarm"
    subject = (
        f"[SciTeX] scitex-storage alarm on {host or socket.gethostname()}: {first}"
    )
    return " ".join(subject.split())[:200]


def email_notifier(profile: str | None = None) -> Notifier:
    """A notifier that mails the alarm as a scitex-notification profile.

    Independent of the fleet database by construction: direct SMTP, no card
    store involved. Returns ``True`` when the SMTP server accepted the
    message (``status: sent``), or when this exact alarm text was already
    accepted inside :data:`EMAIL_DEDUPE_WINDOW_S` (``suppressed``). An
    unusable profile (missing variable, unreadable or group-readable
    password file) or an SMTP error RAISES, so the rail chain names it.
    SMTP acceptance is the strongest confirmation this transport can give;
    it is not proof of reading.
    """

    def send(text: str) -> bool | None:
        _get_profile, send_with_profile = _notification_api()
        result = send_with_profile(
            resolve_email_profile(profile),
            email_subject(text),
            text,
            dedupe_key=email_event_id(text),
            dedupe_window_s=EMAIL_DEDUPE_WINDOW_S,
        )
        status = result.get("status")
        if status in ("sent", "suppressed"):
            return True
        raise RuntimeError(f"email profile send returned status={status!r}")

    return send


def check_email_rail(profile: str | None = None) -> dict:
    """Validate the email rail WITHOUT sending.

    Resolves the profile (every missing or invalid variable is named) and
    runs the package's ``dry_run``, which checks the message and the dedupe
    state but opens no connection. It therefore proves configuration, not
    that the SMTP server accepts the credential. The credential itself is
    never returned, only ``has_password``.
    """
    name = resolve_email_profile(profile)
    base = {"ok": False, "profile": name}
    try:
        get_profile, send_with_profile = _notification_api()
        resolved = get_profile(name)
        send_with_profile(
            resolved,
            "[SciTeX] scitex-storage alarm rail check (dry run)",
            "Dry-run validation of the scitex-storage alarm email rail.\n",
            dry_run=True,
        )
    except Exception as exc:  # noqa: BLE001 -- a broken rail is a failed check
        return {**base, "detail": f"{type(exc).__name__}: {exc}"}
    info = {k: v for k, v in resolved.describe().items() if k != "profile"}
    if not info.get("has_password"):
        return {
            **base,
            **info,
            "detail": f"profile {name!r} has no SMTP password; "
            "the server would refuse an unauthenticated submission",
        }
    return {
        **base,
        **info,
        "ok": True,
        "detail": f"profile {name!r}: From {info.get('from')} To {info.get('to')} "
        f"via {info.get('smtp_host')}:{info.get('smtp_port')} "
        "(configuration only; SMTP login not attempted)",
    }


# EOF
