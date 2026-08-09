#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_alarm/__init__.py
"""Storage alarms: decide what is wrong, and TELL SOMEONE.

A subpackage rather than two flat modules, and that is not a style
preference. ``src/scitex_storage/`` carries a standing rule, recorded in
this repo's own PS-108b skip-rule rationale:

    HARD RULE FROM HERE: no further module may be added at this root
    until that refactor lands.

The first version of this feature added ``_alarm.py`` and
``_alarm_notify.py`` at that root, taking the flat count 21 -> 23 and
breaking the rule. CI caught it. The same rationale had already predicted
exactly this failure -- "the reason stays plausible while the number it is
about keeps moving, so nobody rereads it" -- and I did not reread it.

The split is the one the code already had:

* :mod:`._decide` -- pure. Thresholds, levels, transitions, message
  rendering. No I/O, so every rule is directly testable.
* :mod:`._notify` -- transport. Delivery and its honest three-valued
  result. Everything that can fail for environmental reasons.

Re-exported here so callers say ``from scitex_storage._alarm import ...``
and never depend on which side of that line a name lives on.
"""

from __future__ import annotations

from ._decide import (  # noqa: F401 -- re-exported public API
    CRITICAL,
    CRITICAL_FREE_BYTES,
    OK,
    UNKNOWN,
    UNKNOWN_STREAK_ALERT,
    WARN,
    WARN_FREE_BYTES,
    FilesystemAlarm,
    FleetAlarm,
    evaluate_row,
    evaluate_snapshot,
    format_alarm,
    format_blindness,
    should_notify,
)
from ._notify import (  # noqa: F401 -- re-exported public API
    Notifier,
    PushResult,
    notify_if_needed,
    operator_dm_notifier,
)

# EOF
