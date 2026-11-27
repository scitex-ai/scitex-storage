#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_alarm/_repeat.py
"""Per-filesystem transitions and repeat suppression. Pure.

:func:`._decide.should_notify` decides on the FLEET level, and that has three
holes that only per-filesystem memory closes (operator, 2026-11-27):

* a second filesystem going WARN while a first is already WARN is invisible,
  because the fleet level did not change;
* a filesystem recovering while another stays WARN is never said;
* a volume flapping around one floor mails on every ten-minute gather.

So the CLI remembers, per filesystem, the level the reader was LAST TOLD
(``announced``) and when each level was last mailed (``sent_at``), and
:func:`decide_filesystems` turns that memory plus a fresh :class:`FleetAlarm`
into one :class:`PushDecision`. Memory is passed in and returned, never read
from disk here, for the same reason ``previous_level`` is: the rule stays
exhaustively testable without a filesystem or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._decide import CRITICAL, OK, UNKNOWN, WARN, FilesystemAlarm, FleetAlarm

#: Default for "the same level at most once per N hours per filesystem".
#: Six hours: a volume flapping across a floor every gather mails at most
#: four times a day, and a problem that returns after a recovery is still
#: re-announced the same working day.
DEFAULT_REPEAT_AFTER_S = 6 * 3600.0

_RANK = {OK: 0, UNKNOWN: 1, WARN: 2, CRITICAL: 3}
_ALARMING = (WARN, CRITICAL)


def filesystem_key(fs: FilesystemAlarm) -> str:
    """The identity a filesystem's memory is stored under."""
    return f"{fs.host}:{fs.mount}"


@dataclass
class PushDecision:
    """Whether this gather mails, why, and the memory to persist after it."""

    send: bool
    #: One line per filesystem that triggered the mail.
    changes: list[str] = field(default_factory=list)
    #: Filesystems whose mail the repeat window held back.
    suppressed: list[str] = field(default_factory=list)
    #: ``{key: {"announced": level|None, "sent_at": {level: epoch_s}}}``.
    #: Persist it only when the push was not refused, so a failed push is
    #: retried on the next gather instead of being remembered as told.
    memory: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        if self.send:
            return "; ".join(self.changes)
        if self.suppressed:
            return "repeat suppressed: " + "; ".join(self.suppressed)
        return "no per-filesystem transition"


def _copy_memory(memory: dict | None) -> dict:
    out: dict = {}
    for key, entry in (memory or {}).items():
        if not isinstance(entry, dict):
            continue  # a corrupt entry is forgotten, which re-announces: loud
        sent = entry.get("sent_at")
        out[key] = {
            "announced": entry.get("announced"),
            "sent_at": dict(sent) if isinstance(sent, dict) else {},
        }
    return out


def decide_filesystems(
    alarm: FleetAlarm,
    memory: dict | None,
    now_s: float,
    repeat_after_s: float = DEFAULT_REPEAT_AFTER_S,
) -> PushDecision:
    """Decide this gather's mail from per-filesystem memory. Pure.

    * WARN/CRITICAL mails when it is worse than what was announced -- a new
      alarm, or WARN -> CRITICAL -- unless the SAME level was already mailed
      for this filesystem within ``repeat_after_s``. The window is per level,
      so a recent WARN mail never delays WARN -> CRITICAL. A held-back alarm
      leaves ``announced`` unchanged, so it is mailed once the window has
      passed if it is still current.
    * OK mails only as a RECOVERY from an announced WARN/CRITICAL; ok -> ok
      and unknown -> ok never mail. Recovery is not windowed: it can only
      follow an alarm mail, so the alarm window already bounds it, and
      suppressing it would leave the reader believing a cleared volume is
      still full.
    * CRITICAL -> WARN does not mail, but ``announced`` drops to WARN so a
      later return to CRITICAL is said (subject to the CRITICAL window).
    * UNKNOWN mails only when we lose sight of an announced WARN/CRITICAL,
      windowed like an alarm. Sustained blindness of a healthy filesystem
      stays the fleet-level streak rule's job.

    A filesystem with no memory (first run, lost state) is announced if it
    is alarming: amnesia must never read as "nothing changed".
    """
    new = _copy_memory(memory)
    changes: list[str] = []
    suppressed: list[str] = []

    for fs in alarm.filesystems:
        key = filesystem_key(fs)
        entry = new.setdefault(key, {"announced": None, "sent_at": {}})
        told = entry["announced"]
        told_alarming = told in _ALARMING
        level = fs.level

        if level == OK:
            entry["announced"] = OK
            if told_alarming:
                entry["sent_at"][OK] = now_s
                changes.append(f"{key} recovered: {told} -> ok")
            continue
        if level in _ALARMING:
            if told == level:
                continue
            if told_alarming and _RANK[level] < _RANK[told]:
                entry["announced"] = level
                continue
            what = (
                f"{key} escalated: {told} -> {level}"
                if told_alarming
                else f"{key} is {level}" + (f" (was {told})" if told else "")
            )
        else:  # UNKNOWN
            if not told_alarming:
                continue
            what = f"{key} lost sight: {told} -> unknown"

        last = entry["sent_at"].get(level)
        if isinstance(last, (int, float)) and now_s - last < repeat_after_s:
            suppressed.append(
                f"{what} (already mailed within {repeat_after_s / 3600:g}h)"
            )
            continue
        entry["announced"] = level
        entry["sent_at"][level] = now_s
        changes.append(what)

    return PushDecision(
        send=bool(changes), changes=changes, suppressed=suppressed, memory=new
    )


def format_changes(changes: list[str]) -> str:
    """The "why this mail" block appended to a pushed alarm."""
    if not changes:
        return ""
    return "\n".join(["changes since the last mail:"] + [f"  {c}" for c in changes])


# EOF
