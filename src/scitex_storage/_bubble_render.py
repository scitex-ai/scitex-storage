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
import re

from ._fleet_status import MEASURED, FleetSnapshot, HostStorage

#: Strip a partition/slice suffix to get the physical container a source
#: device belongs to. macOS APFS volumes ``/dev/disk3s1s1``,
#: ``/dev/disk3s5`` ... all share container ``/dev/disk3``; Linux
#: ``/dev/sda1`` -> ``/dev/sda``, ``/dev/nvme0n1p2`` -> ``/dev/nvme0n1``.
#: On Linux each real filesystem has a distinct device, so this collapse
#: is a no-op there and only folds the APFS case it exists for.
_SLICE_RE = re.compile(r"^(/dev/disk\d+|/dev/nvme\d+n\d+|/dev/[a-z]+\d*[a-z]*)")


def _container(source: str) -> str:
    """The physical container a device belongs to, for de-duplication.

    APFS presents one SSD as many volumes, each reporting the container's
    full size; summing them multi-counts capacity (mba read 2.7T for a
    245G disk). Folding by container counts each disk once. Falls back to
    the raw source when the shape is unfamiliar -- an unknown device is
    its own container, which never over-collapses.
    """
    m = _SLICE_RE.match(source)
    if not m:
        return source
    base = m.group(1)
    # /dev/sda1 -> /dev/sda (trailing partition digits), but keep
    # /dev/nvme0n1 intact (handled by the alternation above).
    if base.startswith("/dev/disk") or "nvme" in base:
        return base
    return re.sub(r"\d+$", "", base)


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
                "containers": {},  # container-id -> folded record
            }
            order.append(row.host)
        if _usable(row):
            _fold_into_container(by_host[row.host]["containers"], row)

    records = []
    for h in order:
        rec = by_host[h]
        filesystems = [_finalise_container(c) for c in rec["containers"].values()]
        filesystems.sort(key=lambda f: -f["total_bytes"])
        total_bytes = sum(f["total_bytes"] for f in filesystems)
        used_bytes = sum(f["used_bytes"] for f in filesystems)
        # Host inode% is the WORST across its filesystems, not an average:
        # inode exhaustion on ANY one filesystem breaks writes there, so
        # the alarm is the max. This is the axis that silently took
        # punim0264 to 97% while its space sat at 70%.
        inode_vals = [f["inode_pct"] for f in filesystems if f["inode_pct"] is not None]
        records.append(
            {
                "host": rec["host"],
                "role": rec["role"],
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "used_pct": (
                    round(100.0 * used_bytes / total_bytes, 1)
                    if total_bytes > 0
                    else None
                ),
                "inode_pct": max(inode_vals) if inode_vals else None,
                "filesystems": [
                    {k: f[k] for k in ("mount", "total_bytes", "used_pct", "inode_pct")}
                    for f in filesystems
                ],
            }
        )
    records.sort(key=lambda r: -r["total_bytes"])
    return records


def _fold_into_container(containers: dict, row: HostStorage) -> None:
    """Merge one filesystem row into its physical container.

    APFS volumes that share a disk report the SAME total and available, so
    the container is counted once: its capacity is that shared total, its
    usage ``total - available`` (both identical across the volumes). Rows
    without ``avail_bytes`` (older callers/tests) fall back to
    ``size * used_pct``, and -- having distinct sources on Linux -- do not
    collapse anyway. The representative mount is the shortest, which is the
    recognisable one (``/`` over ``/System/Volumes/Data``).
    """
    cid = _container(row.source) if row.source else row.mount
    c = containers.get(cid)
    if c is None:
        c = {"mount": row.mount, "total_bytes": 0, "avail_bytes": None,
             "used_pct_row": row.used_pct, "inode_pcts": []}
        containers[cid] = c
    if len(row.mount) < len(c["mount"]):
        c["mount"] = row.mount
    # The container total is the shared size; guard against a rounding
    # blip by keeping the max seen.
    c["total_bytes"] = max(c["total_bytes"], row.size_bytes)
    if row.avail_bytes is not None:
        c["avail_bytes"] = row.avail_bytes
    c["used_pct_row"] = row.used_pct
    if row.inode_used_pct is not None:
        c["inode_pcts"].append(row.inode_used_pct)


def _finalise_container(c: dict) -> dict:
    """Turn a folded container into a filesystem record with real bytes used."""
    total = c["total_bytes"]
    if c["avail_bytes"] is not None:
        used = max(0, total - c["avail_bytes"])
    else:
        used = int(total * (c["used_pct_row"] / 100.0))
    return {
        "mount": c["mount"],
        "total_bytes": total,
        "used_bytes": used,
        "used_pct": round(100.0 * used / total, 1) if total > 0 else None,
        "inode_pct": max(c["inode_pcts"]) if c["inode_pcts"] else None,
    }


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
