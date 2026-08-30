#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure HTML renderer for the fleet storage dashboard.

Snapshot dataclasses in, a self-contained dark-mode HTML string out —
zero I/O, so every threshold / three-state / dark-mode case is testable by
constructing plain dataclasses (PA-306: no mocks). Kept in its own module
so :mod:`scitex_storage._fleet_status` stays under the file-size limit; the
public entry point :func:`build_dashboard_html` is re-exported from there,
so callers still import it from ``scitex_storage._fleet_status``.
"""

from __future__ import annotations

import html

from .._fleet_status import FleetSnapshot, HostStorage, _now_iso
from .._measure._inodes import COULD_NOT_LOOK, MEASURED, NOT_APPLICABLE

# Inline, self-contained, dark by default (the operator's eyes are light
# sensitive — see the constitution). No external assets, no CDN, no fonts.
_CSS = """\
:root {
  --bg: #12151a; --panel: #1b2027; --panel-2: #232a33; --border: #2c333d;
  --text: #d7dde5; --muted: #8b95a3; --accent: #4ea1ff;
  --ok: #3fb950; --warn: #d29922; --crit: #f85149; --unknown: #6e7681;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.5;
}
h1 { font-size: 20px; margin: 0 0 4px; font-weight: 600; }
.gen { color: var(--muted); font-size: 12px; margin: 0 0 18px; }
.summary { display: flex; gap: 12px; flex-wrap: wrap; margin: 0 0 22px; }
.stat {
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 16px; min-width: 120px;
}
.stat .n { font-size: 22px; font-weight: 700; }
.stat .l { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; }
.stat.crit .n { color: var(--crit); }
.stat.unknown .n { color: var(--unknown); }
.role-group { margin: 0 0 20px; }
.role-head {
  font-size: 12px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--accent); margin: 0 0 8px; font-weight: 600;
}
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; font-weight: 600; }
tr.flagged td { background: rgba(248, 81, 73, 0.08); }
td.host { font-weight: 600; }
td.mount { color: var(--muted); font-family: ui-monospace, "SFMono-Regular",
  Menlo, Consolas, monospace; font-size: 12px; }
.bar-wrap { display: flex; align-items: center; gap: 8px; min-width: 160px; }
.bar {
  position: relative; flex: 1; height: 10px; background: var(--panel-2);
  border-radius: 5px; overflow: hidden;
}
.bar > span { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px; }
.bar > span.ok { background: var(--ok); }
.bar > span.warn { background: var(--warn); }
.bar > span.crit { background: var(--crit); }
.pct { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; }
.pct.crit { color: var(--crit); font-weight: 600; }
.na { color: var(--unknown); font-style: italic; }
.donut-wrap { display: flex; align-items: center; justify-content: center; min-width: 60px; }
.donut { transform: rotate(0deg); }
.donut-bg { stroke: var(--panel-2); }
.donut-arc { transition: none; }
.donut-arc.ok { stroke: var(--ok); }
.donut-arc.warn { stroke: var(--warn); }
.donut-arc.crit { stroke: var(--crit); }
.donut-label { font-size: 11px; font-weight: 600; fill: var(--text); font-variant-numeric: tabular-nums; }
.donut-label.crit { fill: var(--crit); }
.verdict { font-size: 12px; }
.verdict.measured { color: var(--muted); }
.verdict.could-not-look { color: var(--unknown); font-weight: 600; }
.verdict.not-applicable { color: var(--unknown); }
.note { color: var(--muted); font-size: 11px; }
footer { color: var(--muted); font-size: 11px; margin-top: 24px; }
"""


def _bar(pct: float | None, *, flagged: bool) -> str:
    """A used-% bar cell. Grey em-dash when the value is not a measurement."""
    if pct is None:
        return '<div class="bar-wrap"><span class="na">&mdash;</span></div>'
    klass = "crit" if flagged else ("warn" if pct >= 70 else "ok")
    width = max(0.0, min(100.0, pct))
    pct_klass = "pct crit" if flagged else "pct"
    return (
        '<div class="bar-wrap">'
        f'<div class="bar"><span class="{klass}" style="width:{width:.1f}%"></span></div>'
        f'<span class="{pct_klass}">{pct:.0f}%</span>'
        "</div>"
    )


def _donut(pct: float | None, *, flagged: bool) -> str:
    """A used-% donut cell as a self-contained inline SVG.

    No JS and no charting library -- the dashboard must open offline
    anywhere, so the chart is drawn with two SVG circles. The radius is
    chosen so the circumference is exactly 100, which makes
    ``stroke-dasharray="{used} {100-used}"`` map percent to arc length
    1:1 with no arithmetic. ``stroke-dashoffset=25`` (a quarter of the
    circumference) rotates the start to twelve o'clock.

    ``None`` is a grey em dash, never a full or empty ring -- the same
    three-state discipline as the bar: an unmeasured value is not a zero.
    """
    if pct is None:
        return '<div class="donut-wrap"><span class="na">&mdash;</span></div>'
    klass = "crit" if flagged else ("warn" if pct >= 70 else "ok")
    used = max(0.0, min(100.0, pct))
    return (
        '<div class="donut-wrap">'
        '<svg class="donut" viewBox="0 0 40 40" width="46" height="46" '
        'role="img">'
        '<circle class="donut-bg" cx="20" cy="20" r="15.915" fill="none" '
        'stroke-width="5"/>'
        f'<circle class="donut-arc {klass}" cx="20" cy="20" r="15.915" '
        f'fill="none" stroke-width="5" stroke-dasharray="{used:.1f} '
        f'{100.0 - used:.1f}" stroke-dashoffset="25" stroke-linecap="round"/>'
        f'<text class="donut-label {klass}" x="20" y="21" text-anchor="middle" '
        f'dominant-baseline="middle">{pct:.0f}%</text>'
        "</svg>"
        "</div>"
    )


_VERDICT_LABEL = {
    MEASURED: "measured",
    NOT_APPLICABLE: "n/a (no inode table)",
    COULD_NOT_LOOK: "could-not-look",
}


def _verdict_cell(row: HostStorage) -> str:
    label = _VERDICT_LABEL.get(row.verdict, row.verdict)
    return f'<span class="verdict {html.escape(row.verdict)}">{html.escape(label)}</span>'


def _row_html(row: HostStorage) -> str:
    tr_class = ' class="flagged"' if row.is_flagged else ""
    space_cell = _donut(row.used_pct, flagged=row.space_flagged)
    inode_cell = _donut(row.inode_used_pct, flagged=row.inode_flagged)
    note = f'<div class="note">{html.escape(row.note)}</div>' if row.note else ""
    return (
        f"<tr{tr_class}>"
        f'<td class="host">{html.escape(row.host)}</td>'
        f'<td class="mount">{html.escape(row.mount)}{note}</td>'
        f"<td>{space_cell}</td>"
        f"<td>{inode_cell}</td>"
        f"<td>{_verdict_cell(row)}</td>"
        "</tr>"
    )


def _group_by_role(rows: list[HostStorage]) -> list[tuple[str, list[HostStorage]]]:
    """Rows grouped by role, roles in first-seen order (stable, scannable)."""
    order: list[str] = []
    groups: dict[str, list[HostStorage]] = {}
    for row in rows:
        if row.role not in groups:
            groups[row.role] = []
            order.append(row.role)
        groups[row.role].append(row)
    return [(role, groups[role]) for role in order]


def _summary_html(snapshot: FleetSnapshot) -> str:
    flagged_class = "stat crit" if snapshot.flagged_count else "stat"
    cnl_class = "stat unknown" if snapshot.could_not_look_count else "stat"
    return "".join(
        [
            '<div class="summary">',
            f'<div class="stat"><div class="n">{snapshot.total_hosts}</div>'
            '<div class="l">Hosts</div></div>',
            f'<div class="stat"><div class="n">{snapshot.total_filesystems}</div>'
            '<div class="l">Filesystems</div></div>',
            f'<div class="{flagged_class}"><div class="n">{snapshot.flagged_count}</div>'
            '<div class="l">Flagged &ge;85%</div></div>',
            f'<div class="{cnl_class}"><div class="n">{snapshot.could_not_look_count}'
            '</div><div class="l">Could not look</div></div>',
            "</div>",
        ]
    )


def build_dashboard_html(snapshot: FleetSnapshot) -> str:
    """Render ``snapshot`` to a self-contained, dark-mode HTML dashboard. Pure.

    No I/O and no external assets: the returned string is a complete
    ``<!doctype html>`` document that opens offline, anywhere. Rows over
    ``FLAG_PERCENT`` on space OR inodes are flagged red; could-not-look
    rows render grey with an em dash and are never shown green. The header
    states total hosts, flagged filesystems, could-not-look filesystems and
    the generation note, so the summary cannot silently disagree with the
    table below it.
    """
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Fleet Storage Dashboard</title>",
        f"<style>{_CSS}</style>",
        "</head><body>",
        "<h1>Fleet Storage Dashboard</h1>",
    ]

    gen = snapshot.generated_at or _now_iso()
    note = f" &middot; {html.escape(snapshot.note)}" if snapshot.note else ""
    parts.append(f'<p class="gen">Generated {html.escape(gen)}{note}</p>')
    parts.append(_summary_html(snapshot))

    if not snapshot.rows:
        parts.append('<p class="note">No filesystems in this snapshot.</p>')
    for role, rows in _group_by_role(snapshot.rows):
        parts.append('<div class="role-group">')
        parts.append(f'<div class="role-head">{html.escape(role)}</div>')
        parts.append("<table><thead><tr>")
        parts.append("<th>Host</th><th>Mount</th><th>Space used</th>")
        parts.append("<th>Inodes used</th><th>Inode verdict</th>")
        parts.append("</tr></thead><tbody>")
        parts.extend(_row_html(row) for row in rows)
        parts.append("</tbody></table></div>")

    parts.append(
        "<footer>scitex-storage fleet-status &mdash; three-state verdicts: "
        "a grey em dash means the inode metric could not be measured "
        "(could-not-look) or does not apply (dynamic inode table), never "
        "that it is fine.</footer>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


# EOF
