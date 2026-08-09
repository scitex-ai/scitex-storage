#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_alarm.py
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

from ._fleet_status import FLAG_PERCENT, FleetSnapshot, HostStorage

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


def format_alarm(alarm: FleetAlarm) -> str:
    """Render the pushed message: what, where, and how bad -- in that order.

    Deliberately plain text and deliberately short. This goes to a phone
    and to a chat pane, and an alarm nobody finishes reading is an alarm
    that did not fire. The unknown count is stated even when nothing is
    alarming, so a reader can tell a quiet fleet from an unmeasured one.
    """
    if not alarm.should_push:
        return f"storage {alarm.level}: nothing alarming ({len(alarm.unknown)} unmeasured)"

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
