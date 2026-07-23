#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_bubble_render.py
"""Capacity-bubble view: one circle per host, area = capacity, fill = usage%.

The operator's brief (2026-07-23): a Codecov-style interactive picture,
but for storage -- "円の大きさはディスク容量を、割合はもちろん割合を、
ホストごとに円". So each host is a donut whose AREA is proportional to its
total capacity (radius scales with the square root of bytes, which is
what makes AREA -- not radius -- track capacity, the honest encoding),
and whose arc fill is its usage%. Click a host to drill into its
filesystems, each drawn the same way.

Self-contained: the data is embedded as JSON and the chart is drawn with
inline SVG by a few dozen lines of vanilla JS -- no D3, no CDN, no
external asset. It opens offline, exactly like the table dashboard, which
is a hard requirement for a page that may be saved and mailed around.

This module is PURE: ``build_bubbles_html(snapshot)`` returns a complete
HTML string and touches no I/O, so every aggregation rule is unit
testable by constructing dataclass rows.
"""

from __future__ import annotations

import html
import json

from ._fleet_status import MEASURED, FleetSnapshot, HostStorage


def _usable(row: HostStorage) -> bool:
    """A row that contributes a real capacity+usage number.

    Structural rows (read-only images) carry a size but no meaningful
    usage; could-not-look rows carry neither trustworthy. Only measured
    rows with both a size and a usage% aggregate into a host's circle --
    everything else would either inflate capacity with pseudo-filesystems
    or invent a usage the probe never established.
    """
    return (
        row.verdict == MEASURED
        and row.size_bytes is not None
        and row.used_pct is not None
    )


def aggregate_hosts(snapshot: FleetSnapshot) -> list[dict]:
    """Aggregate rows into one capacity record per host.

    Returns dicts (JSON-ready) sorted by capacity descending, each with
    the host's total/used bytes, weighted usage%, and its per-filesystem
    breakdown for drill-down. A host with no usable filesystem still
    appears, with ``total_bytes == 0`` and an empty breakdown, because a
    host that dropped off the capacity view entirely would read as "gone"
    when it may simply be unreadable right now.
    """
    by_host: dict[str, dict] = {}
    order: list[str] = []
    for row in snapshot.rows:
        if row.host not in by_host:
            by_host[row.host] = {
                "host": row.host,
                "role": row.role,
                "total_bytes": 0,
                "used_bytes": 0,
                "filesystems": [],
            }
            order.append(row.host)
        rec = by_host[row.host]
        if _usable(row):
            used_bytes = int(row.size_bytes * (row.used_pct / 100.0))
            rec["total_bytes"] += row.size_bytes
            rec["used_bytes"] += used_bytes
            rec["filesystems"].append(
                {
                    "mount": row.mount,
                    "total_bytes": row.size_bytes,
                    "used_pct": round(row.used_pct, 1),
                }
            )

    records = [by_host[h] for h in order]
    for rec in records:
        rec["used_pct"] = (
            round(100.0 * rec["used_bytes"] / rec["total_bytes"], 1)
            if rec["total_bytes"] > 0
            else None
        )
        rec["filesystems"].sort(key=lambda f: -f["total_bytes"])
    records.sort(key=lambda r: -r["total_bytes"])
    return records


_PAGE = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Capacity</title>
<style>
:root {{
  --bg:#12151a; --panel:#1b2027; --text:#d7dde5; --muted:#8b95a3;
  --ring:#2c333d; --ok:#3fb950; --warn:#d29922; --crit:#f85149;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--text);
  font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
.gen {{ color:var(--muted); font-size:12px; margin:0 0 16px; }}
#crumb {{ font-size:14px; margin-bottom:12px; min-height:22px; }}
#crumb a {{ color:var(--accent,#4ea1ff); cursor:pointer; text-decoration:none; }}
#stage {{ display:flex; flex-wrap:wrap; gap:20px; align-items:flex-end; }}
.bub {{ cursor:pointer; text-align:center; }}
.bub text {{ fill:var(--text); font-variant-numeric:tabular-nums; }}
.cap {{ color:var(--muted); font-size:12px; margin-top:2px; }}
.name {{ font-size:13px; font-weight:600; margin-top:6px; }}
#tip {{ position:fixed; pointer-events:none; background:var(--panel);
  border:1px solid var(--ring); border-radius:8px; padding:8px 10px;
  font-size:12px; opacity:0; transition:opacity .1s; max-width:320px; z-index:9; }}
#tip b {{ color:var(--text); }}
footer {{ color:var(--muted); font-size:11px; margin-top:28px; }}
</style></head><body>
<h1>Fleet Capacity</h1>
<p class="gen">{gen}</p>
<div id="crumb"></div>
<div id="stage"></div>
<div id="tip"></div>
<footer>Circle area = total capacity &middot; ring fill = used %% &middot;
click a host to drill into its filesystems. Self-contained: no external
libraries, opens offline.</footer>
<script>
const HOSTS = {data};
function human(b){{const u=['B','KB','MB','GB','TB','PB'];let i=0,n=b;
  while(n>=1024&&i<u.length-1){{n/=1024;i++;}}return n.toFixed(n<10?1:0)+u[i];}}
function colour(p){{if(p==null)return '#6e7681';return p>=85?'#f85149':p>=70?'#d29922':'#3fb950';}}
const stage=document.getElementById('stage');
const crumb=document.getElementById('crumb');
const tip=document.getElementById('tip');
const MAXR=90, MINR=26;
function radius(bytes,maxBytes){{if(!maxBytes)return MINR;
  return MINR+(MAXR-MINR)*Math.sqrt(bytes/maxBytes);}}
function donut(cx,cy,r,pct,label,sub){{
  const rr=r-6, C=2*Math.PI*rr, used=(pct==null?0:Math.max(0,Math.min(100,pct)));
  const dash=C*used/100;
  return `<svg width="${{2*r}}" height="${{2*r}}" viewBox="0 0 ${{2*r}} ${{2*r}}">
    <circle cx="${{r}}" cy="${{r}}" r="${{rr}}" fill="none" stroke="#232a33" stroke-width="10"/>
    <circle cx="${{r}}" cy="${{r}}" r="${{rr}}" fill="none" stroke="${{colour(pct)}}"
      stroke-width="10" stroke-linecap="round"
      stroke-dasharray="${{dash}} ${{C-dash}}" stroke-dashoffset="${{C/4}}"
      transform="rotate(-90 ${{r}} ${{r}})"/>
    <text x="${{r}}" y="${{r-2}}" text-anchor="middle" font-size="15" font-weight="700">${{label}}</text>
    <text x="${{r}}" y="${{r+16}}" text-anchor="middle" font-size="11" fill="#8b95a3">${{sub}}</text>
  </svg>`;
}}
function showTip(e,htmlStr){{tip.innerHTML=htmlStr; tip.style.opacity=1;
  tip.style.left=Math.min(e.clientX+14,innerWidth-330)+'px'; tip.style.top=(e.clientY+14)+'px';}}
function hideTip(){{tip.style.opacity=0;}}
function bubbleEl(r,pct,label,sub,name,tipHtml,onClick){{
  const d=document.createElement('div'); d.className='bub';
  d.innerHTML=donut(0,0,r,pct,label,sub)+`<div class="name">${{name}}</div>`;
  d.onmousemove=e=>showTip(e,tipHtml); d.onmouseleave=hideTip;
  if(onClick){{d.onclick=onClick;}} return d;
}}
function renderHosts(){{
  crumb.innerHTML='<b>Fleet</b>';
  stage.innerHTML='';
  const withCap=HOSTS.filter(h=>h.total_bytes>0);
  const maxB=Math.max(1,...withCap.map(h=>h.total_bytes));
  for(const h of HOSTS){{
    const r=radius(h.total_bytes,maxB);
    const pctLabel=h.used_pct==null?'&mdash;':h.used_pct+'%';
    const fsList=h.filesystems.map(f=>`${{f.mount}} &mdash; ${{human(f.total_bytes)}} (${{f.used_pct}}%)`).join('<br>');
    const tipHtml=`<b>${{h.host}}</b> (${{h.role}})<br>capacity ${{human(h.total_bytes)}}<br>`+
      `used ${{h.used_pct==null?'unknown':h.used_pct+'%'}}<br><br>${{fsList||'no readable filesystem'}}`;
    stage.appendChild(bubbleEl(r,h.used_pct,pctLabel,human(h.total_bytes),h.host,tipHtml,
      h.filesystems.length?()=>renderHost(h):null));
  }}
}}
function renderHost(h){{
  crumb.innerHTML='<a id="back">&larr; Fleet</a> / <b>'+h.host+'</b>';
  document.getElementById('back').onclick=renderHosts;
  stage.innerHTML='';
  const maxB=Math.max(1,...h.filesystems.map(f=>f.total_bytes));
  for(const f of h.filesystems){{
    const r=radius(f.total_bytes,maxB);
    const tipHtml=`<b>${{f.mount}}</b><br>capacity ${{human(f.total_bytes)}}<br>used ${{f.used_pct}}%`;
    stage.appendChild(bubbleEl(r,f.used_pct,f.used_pct+'%',human(f.total_bytes),f.mount,tipHtml,null));
  }}
}}
renderHosts();
</script>
</body></html>
"""


def build_bubbles_html(snapshot: FleetSnapshot) -> str:
    """Render ``snapshot`` as the interactive capacity-bubble page. Pure."""
    records = aggregate_hosts(snapshot)
    gen = html.escape(snapshot.generated_at or "")
    note = html.escape(snapshot.note or "")
    gen_line = f"Generated {gen}" + (f" &middot; {note}" if note else "")
    # json.dumps output is safe inside a <script> except for a literal
    # "</script>"; escape the slash so an adversarial mount name cannot
    # close the tag early.
    data = json.dumps(records).replace("</", "<\\/")
    return _PAGE.format(gen=gen_line, data=data)

# EOF
