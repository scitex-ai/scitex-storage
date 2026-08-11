#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_alarm/_decide.py
"""Turn a fleet snapshot into a PUSHED alarm -- the half a dashboard cannot do.

The observation layer already measures everything this needs:
:mod:`._observe` gathers the fleet, :class:`._fleet_status.HostStorage`
carries space and inode percentages with a three-state verdict, and
:data:`._fleet_status.FLAG_PERCENT` already decides what counts as red.
What did not exist was anything that *tells someone*. The gather renders
HTML to a file and stops; reading it requires already suspecting there is
something to read.

WHY THAT GAP IS THE WHOLE DEFECT (measured, 2026-08-09): scitex-compute-04
went to 364 MB free on a 393 GB volume -- 100% -- and NOTHING reported it.
It surfaced only because a routine ``head`` inside an unrelated five-minute
cron on another agent happened to write and died with ENOSPC. The detection
mechanism was "an agent happens to run a command that writes". The next
occurrence corrupts a SQLite mid-transaction instead of killing a text
filter, and the host carries sac's state DB. A dashboard would not have
helped: nobody was looking at it, which is what a dashboard is for.

TWO THRESHOLD FAMILIES, because percentage alone is not sufficient:

* PERCENT (:data:`._fleet_status.FLAG_PERCENT`, 85) scales across a fleet
  whose volumes span three orders of magnitude, and is reused here rather
  than redefined -- two thresholds for one concept drift apart, and then
  the dashboard and the alarm disagree about whether the fleet is healthy.
* ABSOLUTE FLOORS (:data:`WARN_FREE_BYTES` / :data:`CRITICAL_FREE_BYTES`)
  answer the question percentage cannot: *how long have I got*. On the
  393 GB volume above, free space fell at ~2 GB per five minutes, so the
  gap between 85% and zero was under three hours. A floor stated in bytes
  is directly comparable against an observed fill RATE; a percentage is
  not until you multiply it out.

INODES ARE FIRST-CLASS HERE, not an afterthought. The original request was
for bytes only. A byte-only alarm is blind to the failure mode that fails
writes while ``df`` still shows free space -- which is the shape of the
long-running punim0264 exhaustion, and the reason that incident stayed
invisible for so long. Inodes get the same two families of threshold.

UNKNOWN IS A LEVEL, NOT A SILENCE. A host that cannot be probed does not
report ``ok``; it reports :data:`UNKNOWN` and is counted separately. The
alarm this module replaces failed precisely by rendering an absence as
reassurance, and today three separate agents each read a local empty view
as a global fact. An alarm that cannot say "I could not look" is the same
defect wearing a different hat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._fleet_status import FLAG_PERCENT, FleetSnapshot, HostStorage

#: Free bytes at/under which a filesystem warns. Chosen against a measured
#: fill rate rather than a round number: scitex-compute-04 fell ~2 GB per
#: five minutes (~24 GB/hour) on 2026-08-09, so 20 GB is a little under an
#: hour of warning at that rate -- enough to act, not so much that it fires
#: constantly on a busy CI host.
WARN_FREE_BYTES = 20 * 1024**3

#: Free bytes at/under which a filesystem is CRITICAL. At the rate above
#: this is roughly twelve minutes from zero. It exists to distinguish
#: "look at this today" from "something is about to break", because an
#: alarm with one level trains its reader to treat every firing as routine.
CRITICAL_FREE_BYTES = 5 * 1024**3

#: Alarm levels, ordered by severity. ``UNKNOWN`` deliberately sorts above
#: ``OK``: not being able to measure a filesystem is more interesting than
#: a filesystem that is fine, and must never be folded into it.
OK = "ok"
UNKNOWN = "unknown"
WARN = "warn"
CRITICAL = "critical"

_SEVERITY = {OK: 0, UNKNOWN: 1, WARN: 2, CRITICAL: 3}


@dataclass
class FilesystemAlarm:
    """One filesystem's alarm state, with the reason it reached that level.

    ``reasons`` is a list rather than a single string because space and
    inodes fail independently and a filesystem can cross both at once.
    Collapsing them would hide the inode half behind the byte half, which
    is exactly the blindness this module exists to remove.
    """

    host: str
    mount: str
    level: str
    reasons: list[str] = field(default_factory=list)
    free_bytes: int | None = None
    used_pct: float | None = None
    inode_used_pct: float | None = None

    @property
    def is_alarming(self) -> bool:
        """True for WARN/CRITICAL. UNKNOWN is reported but is not an alarm."""
        return self.level in (WARN, CRITICAL)


@dataclass
class FleetAlarm:
    """The whole-fleet verdict: a FIXED shape, every signal its own field.

    Returned identically whether the fleet is healthy, on fire, or
    unmeasurable, so a caller never has to guess which key exists on this
    particular call. ``level`` is the max over all filesystems, and
    ``unknown`` rows are counted apart from both healthy and alarming ones.
    """

    level: str = OK
    generated_at: str = ""
    filesystems: list[FilesystemAlarm] = field(default_factory=list)

    @property
    def alarming(self) -> list[FilesystemAlarm]:
        return [f for f in self.filesystems if f.is_alarming]

    @property
    def unknown(self) -> list[FilesystemAlarm]:
        return [f for f in self.filesystems if f.level == UNKNOWN]

    @property
    def should_push(self) -> bool:
        """True when there is something a human must be told about.

        UNKNOWN alone does not push. A single unreachable host is a normal
        transient (a NAS asleep, an ssh blip) and paging on it would train
        the reader to ignore the channel -- the failure mode this module is
        built to avoid. It is still carried in the payload, so a reader who
        is already looking sees it.
        """
        return bool(self.alarming)


def _free_bytes(row: HostStorage) -> int | None:
    """Available bytes for a row, or None when it was not measured.

    ``None`` rather than 0: zero free bytes is a measurement meaning "full"
    and must alarm loudly, while "not measured" must not masquerade as the
    most alarming possible reading.
    """
    return row.avail_bytes


def evaluate_row(row: HostStorage) -> FilesystemAlarm:
    """Classify one filesystem. Pure -- no I/O, directly unit-testable.

    Order matters: the level is the WORST of the applicable rules, so a
    filesystem under the critical floor is CRITICAL even if its percentage
    is unremarkable (a very large volume with a little left), and one over
    the percentage threshold is WARN even when its absolute free space
    still looks generous (a small volume nearly full).
    """
    reasons: list[str] = []
    level = OK

    def escalate(new: str, reason: str) -> None:
        nonlocal level
        reasons.append(reason)
        if _SEVERITY[new] > _SEVERITY[level]:
            level = new

    free = _free_bytes(row)
    measured_anything = False

    if free is not None:
        measured_anything = True
        if free <= CRITICAL_FREE_BYTES:
            escalate(CRITICAL, f"{_gib(free)} free (<= {_gib(CRITICAL_FREE_BYTES)})")
        elif free <= WARN_FREE_BYTES:
            escalate(WARN, f"{_gib(free)} free (<= {_gib(WARN_FREE_BYTES)})")

    if row.used_pct is not None:
        measured_anything = True
        if row.used_pct >= FLAG_PERCENT:
            escalate(WARN, f"space {row.used_pct:.1f}% used (>= {FLAG_PERCENT:.0f}%)")

    if row.inode_used_pct is not None:
        measured_anything = True
        if row.inode_used_pct >= FLAG_PERCENT:
            escalate(
                WARN,
                f"inodes {row.inode_used_pct:.1f}% used (>= {FLAG_PERCENT:.0f}%) "
                "-- fails writes while df still shows free space",
            )

    if not measured_anything:
        level = UNKNOWN
        reasons.append(row.note or "no space or inode measurement available")

    return FilesystemAlarm(
        host=row.host,
        mount=row.mount,
        level=level,
        reasons=reasons,
        free_bytes=free,
        used_pct=row.used_pct,
        inode_used_pct=row.inode_used_pct,
    )


def evaluate_snapshot(snapshot: FleetSnapshot) -> FleetAlarm:
    """Classify a whole snapshot. Pure.

    An EMPTY snapshot yields ``UNKNOWN``, never ``OK``. "We measured every
    filesystem and all are healthy" and "we measured nothing" are different
    answers, and only one of them is good news; a gather that silently
    returned no rows must not read as a clean bill of health.
    """
    filesystems = [evaluate_row(r) for r in snapshot.rows]
    if not filesystems:
        return FleetAlarm(
            level=UNKNOWN,
            generated_at=snapshot.generated_at,
            filesystems=[],
        )
    level = max((f.level for f in filesystems), key=lambda lv: _SEVERITY[lv])
    return FleetAlarm(
        level=level,
        generated_at=snapshot.generated_at,
        filesystems=filesystems,
    )


def _gib(num_bytes: int) -> str:
    """Human-readable GiB, one decimal. Alarm text is read under pressure."""
    return f"{num_bytes / 1024**3:.1f} GiB"


#: Consecutive gathers of UNKNOWN after which sustained blindness is
#: announced once. Not a severity number and not tunable per-level -- it
#: answers "how long is a transient", and three gathers is long enough that
#: an ssh blip or a sleeping NAS has cleared, short enough that a host which
#: has genuinely dropped off the fleet is reported the same hour.
UNKNOWN_STREAK_ALERT = 3


def should_notify(
    previous_level: str | None,
    current_level: str,
    unknown_streak: int = 0,
) -> bool:
    """Decide whether THIS gather is worth interrupting a human for. Pure.

    A gather runs every few minutes. Pushing on every cycle while a disk is
    full is how a channel becomes noise, and a reader who has learned to
    swipe the alarm away is exactly as uninformed as one who never got it --
    the failure this whole module exists to prevent, arriving a week later.

    So the rule is TRANSITIONS, not states:

    * escalation always notifies (ok -> warn, warn -> critical), because
      "worse than last time" is new information;
    * RECOVERY notifies exactly once (warn/critical -> ok), because an
      alarm that never says "resolved" leaves the reader unable to tell a
      fixed problem from a forgotten one -- and today's incident did clear
      on its own, with nobody told either way;
    * a level that has not changed does NOT notify, even when it is
      critical. The state is still in the payload for anyone who looks;
      what it stops being is an interruption.

    ``previous_level=None`` means "no prior state recorded" (first run, or
    the state file was lost). That notifies if there is anything to say,
    because a fresh process must not stay silent about a disk that is
    already full -- an alarm whose memory loss reads as "nothing changed"
    is a gate that cannot fail.

    De-escalation between two alarming levels (critical -> warn) is
    deliberately NOT notified: it is still alarming, the reader has already
    been told, and "slightly less on fire" does not need a push.

    UNKNOWN IS NOT RANKED HERE, and the cases are spelled out rather than
    computed from :data:`_SEVERITY`. A first version did rank it -- unknown
    sorted above ok -- so a single unreachable host paged on every gather,
    contradicting :attr:`FleetAlarm.should_push`, which treats unknown as
    not-alarming. Two places deciding one concept, disagreeing. Caught by
    the tests; kept as named branches so the next reader cannot silently
    reintroduce it by adjusting a number.

    The subtle case, and the reason this is not simply "unknown never
    notifies": LOSING SIGHT of a filesystem that was ALARMING does notify.
    Going critical -> unknown is not recovery, and staying silent would let
    the reader infer it was resolved -- false reassurance, the worst
    direction. Going ok -> unknown stays silent, because that is the
    ordinary transient this rule exists to absorb.

    SUSTAINED BLINDNESS IS ITS OWN TRANSITION, and it closes a hole that the
    transition rule alone has. Under "unchanged levels stay silent",
    ``ok -> unknown -> unknown -> ...`` is silent FOREVER: a filesystem that
    permanently drops out of the gather is never mentioned again. But a
    filesystem nobody can read is not fine, it is UNMONITORED -- and an
    unmonitored filesystem nobody is told about is precisely the defect this
    module exists for, arriving in a narrower form. So after
    :data:`UNKNOWN_STREAK_ALERT` consecutive unknown gathers we say so, once.
    (Found by scitex-db reviewing the rule, not by the tests.)

    That page is a DIFFERENT SENTENCE from a capacity alarm -- "no reading
    from this filesystem since T" rather than "this filesystem is full" --
    and :func:`format_alarm` renders it as such. ``unknown_streak`` counts
    consecutive unknown gathers INCLUDING this one; the caller owns the
    counter for the same reason it owns ``previous_level``.
    """
    if current_level == UNKNOWN and unknown_streak == UNKNOWN_STREAK_ALERT:
        # Exactly at the threshold, so this announces once rather than on
        # every subsequent gather -- the same "told once" discipline a
        # sustained critical gets.
        return True
    if previous_level == current_level:
        return False
    if previous_level is None:
        # No prior state: speak only if there is an actual alarm. A fresh
        # process must not announce an unmeasurable fleet as if it were news.
        return current_level in (WARN, CRITICAL)
    was_alarming = previous_level in (WARN, CRITICAL)
    if current_level in (WARN, CRITICAL):
        # New alarm, or a worse one than the reader was last told about.
        return not was_alarming or _SEVERITY[current_level] > _SEVERITY[previous_level]
    if current_level == OK:
        # Recovery, exactly once, and only from something actually reported.
        return was_alarming
    # current_level is UNKNOWN: only worth saying when we just lost sight
    # of something that WAS alarming.
    return was_alarming


def format_blindness(alarm: FleetAlarm, unknown_streak: int) -> str:
    """The sustained-blindness page. A DIFFERENT SENTENCE from a capacity alarm.

    "We have had no reading from these filesystems" and "these filesystems
    are full" call for different actions from different people, so they must
    not share wording. A reader who has been trained by capacity alarms will
    skim this one; naming the filesystems and the streak is what stops it
    reading as a quieter version of the same thing.
    """
    names = ", ".join(f"{f.host}:{f.mount}" for f in alarm.unknown) or "(none named)"
    return (
        f"STORAGE UNMONITORED @ {alarm.generated_at}\n"
        f"  no reading from {names} for {unknown_streak} consecutive gathers.\n"
        "  This is not a capacity alarm: these filesystems are not known to be "
        "healthy or unhealthy, only unread."
    )


def format_alarm(alarm: FleetAlarm) -> str:
    """Render the pushed message: what, where, and how bad -- in that order.

    Deliberately plain text and deliberately short. This goes to a phone
    and to a chat pane, and an alarm nobody finishes reading is an alarm
    that did not fire. The unknown count is stated even when nothing is
    alarming, so a reader can tell a quiet fleet from an unmeasured one.
    """
    if not alarm.should_push:
        # STATE THE DENOMINATOR, not just the numerator. The line above used to
        # read "storage ok: nothing alarming (0 unmeasured)", which is the same
        # sentence whether two local filesystems were checked or the whole
        # fleet was. Measured 2026-08-11: run in a container it reported
        # exactly that while the three NAS units were unreachable (ssh rc=255)
        # and compute-04 -- the host whose free space this alarm was written
        # for -- was never in scope at all. `gather_fleet_snapshot` is honestly
        # documented as local-only ("live multi-host gathering is a later
        # increment"); the MESSAGE was the part that implied fleet coverage.
        #
        # "0 unmeasured" is the dangerous half: it invites the reading that
        # nothing escaped measurement, when the truth is those hosts were never
        # counted. A reader cannot audit a denominator that is not printed.
        hosts = sorted({fs.host for fs in alarm.filesystems})
        scope = hosts[0] if len(hosts) == 1 else f"{len(hosts)} hosts"
        return (
            f"storage {alarm.level}: {len(alarm.filesystems)} filesystem(s) "
            f"on {scope} checked, nothing alarming "
            f"({len(alarm.unknown)} unmeasured)"
        )

    lines = [f"STORAGE {alarm.level.upper()} @ {alarm.generated_at}"]
    for fs in sorted(
        alarm.alarming, key=lambda f: _SEVERITY[f.level], reverse=True
    ):
        lines.append(f"  [{fs.level}] {fs.host}:{fs.mount} -- {'; '.join(fs.reasons)}")
    if alarm.unknown:
        lines.append(
            f"  ({len(alarm.unknown)} filesystem(s) could not be measured -- "
            "not counted as healthy)"
        )
    return "\n".join(lines)

# EOF
