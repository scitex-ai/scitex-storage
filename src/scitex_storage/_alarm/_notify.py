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

ONE RAIL, LOUDLY, RATHER THAN SILENT FALLBACK. There is deliberately no
"try the DM, then try a card, then try a file" chain. A fallback that
quietly succeeds on rail three hides that rails one and two are broken,
and the next incident finds all three rotten. If the configured rail
fails, that is reported as a failure and the caller decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ._decide import (
    UNKNOWN,
    FleetAlarm,
    format_alarm,
    format_blindness,
    should_notify,
)

#: A transport: takes the rendered alarm text, returns whether it landed.
#: ``None`` means "completed, but this rail cannot confirm delivery".
Notifier = Callable[[str], Optional[bool]]


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

    @property
    def is_failure(self) -> bool:
        """True only for an attempted push the transport actively refused.

        ``delivered=None`` is NOT a failure -- it is an unknown, and
        treating it as either pole is the error this class exists to avoid.
        """
        return self.attempted and self.delivered is False


def notify_if_needed(
    alarm: FleetAlarm,
    notifier: Notifier,
    previous_level: str | None = None,
    unknown_streak: int = 0,
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
    """
    if not should_notify(previous_level, alarm.level, unknown_streak):
        return PushResult(
            attempted=False,
            delivered=None,
            level=alarm.level,
            detail=f"no transition ({previous_level} -> {alarm.level})",
        )

    # Sustained blindness gets its own wording. Reusing the capacity text
    # would tell the reader a filesystem is healthy-or-not when the true
    # statement is that we have not read it at all.
    text = (
        format_blindness(alarm, unknown_streak)
        if alarm.level == UNKNOWN
        else format_alarm(alarm)
    )
    try:
        delivered = notifier(text)
    except Exception as exc:  # noqa: BLE001 -- see docstring: never crash the gather
        return PushResult(
            attempted=True,
            delivered=False,
            level=alarm.level,
            text=text,
            detail=f"notifier raised {type(exc).__name__}: {exc}",
        )

    return PushResult(
        attempted=True,
        delivered=delivered,
        level=alarm.level,
        text=text,
        detail=(
            "delivered"
            if delivered
            else "transport could not confirm delivery"
            if delivered is None
            else "transport reported failure"
        ),
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
        from scitex_cards import dm_send  # deferred: optional dependency

        dm_send(to=to, body=text)
        return None  # written to the store; being read is not established

    return send

# EOF
