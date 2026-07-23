#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_sunburst_render.py
"""Codecov-style nested sunburst of fleet capacity.

The operator showed Codecov's radial sunburst as the target: concentric
rings for tree depth, each segment's ANGLE proportional to size and its
COLOUR to a metric, click a segment to zoom, breadcrumb to climb back.
Mapped to storage: inner ring = hosts, outer ring = each host's
filesystems, angle proportional to capacity, colour to usage% (green ->
amber -> red, grey when unmeasured). Click a host to make it the centre;
the breadcrumb returns to the fleet.

The Python side is PURE and does the minimum: it emits the capacity
hierarchy as JSON and lets the browser draw the arcs, exactly as Codecov
does -- the geometry and interaction are ~90 lines of vanilla JS, no D3,
no CDN, so the page opens offline like every other view. The embedded
JSON escapes ``</`` so an adversarial mount name cannot close the script
tag early.
"""

from __future__ import annotations

import html
import json

from ._bubble_render import aggregate_hosts
from ._fleet_status import FleetSnapshot


def build_hierarchy(snapshot: FleetSnapshot) -> dict:
    """Fleet -> hosts -> filesystems, each node carrying capacity + usage.

    Reuses the bubble aggregation (measured-only capacity, weighted
    usage) so the sunburst and the circles can never disagree about a
    host's numbers. A node's ``value`` is its capacity in bytes -- that
    is what the angular width encodes; ``usage`` drives colour and is
    ``None`` when the probe never established it (drawn grey, never green).
    """
    hosts = aggregate_hosts(snapshot)
    children = []
    for h in hosts:
        children.append(
            {
                "name": h["host"],
                "value": h["total_bytes"],
                "usage": h["used_pct"],
                "inode": h["inode_pct"],
                "role": h["role"],
                "children": [
                    {
                        "name": f["mount"],
                        "value": f["total_bytes"],
                        "usage": f["used_pct"],
                        "inode": f["inode_pct"],
                        "children": [],
                    }
                    for f in h["filesystems"]
                ],
            }
        )
    total = sum(c["value"] for c in children)
    return {
        "name": "Fleet",
        "value": total,
        "usage": None,
        "inode": None,
        "children": children,
    }


_PAGE = """\
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Capacity Sunburst</title>
<style>
:root {{ --bg:#12151a; --panel:#1b2027; --text:#d7dde5; --muted:#8b95a3; --ring:#2c333d; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--text);
  font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; text-align:center; }}
svg text {{ user-select:none; -webkit-user-select:none; }}
#sb {{ cursor:default; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
.gen {{ color:var(--muted); font-size:12px; margin:0 0 10px; }}
#modes {{ margin-bottom:8px; }}
#modes button {{ background:var(--panel); color:var(--muted); border:1px solid var(--ring);
  border-radius:6px; padding:5px 12px; margin:0 3px; font-size:12px; cursor:pointer; }}
#modes button.on {{ color:var(--text); border-color:#4ea1ff; }}
#crumb {{ font-size:14px; min-height:22px; margin-bottom:6px; }}
#crumb a {{ color:#4ea1ff; cursor:pointer; }}
svg {{ max-width:100%; height:auto; }}
path.seg {{ cursor:pointer; stroke:#12151a; stroke-width:1; transition:opacity .1s; }}
path.seg:hover {{ opacity:.82; }}
#centre {{ font-size:13px; }}
#tip {{ position:fixed; pointer-events:none; background:var(--panel); border:1px solid var(--ring);
  border-radius:8px; padding:8px 10px; font-size:12px; opacity:0; transition:opacity .1s; z-index:9; text-align:left; }}
footer {{ color:var(--muted); font-size:11px; margin-top:20px; }}
</style></head><body>
<h1>Fleet Capacity Sunburst</h1>
<p class="gen">{gen}</p>
<div id="modes">
  <button id="m-usage" class="on">Colour: Space&nbsp;%%</button>
  <button id="m-inode">Colour: Inodes&nbsp;%%</button>
</div>
<div id="crumb"></div>
<svg id="sb" width="560" height="560" viewBox="0 0 560 560"></svg>
<div id="tip"></div>
<footer>Angle = capacity &middot; colour = the selected metric (green&rarr;red, grey = unmeasured) &middot;
click a host to zoom, the breadcrumb climbs back. Self-contained, opens offline.</footer>
<script>
const ROOT = {data};
const CX=280, CY=280, R0=70, RW=95, TAU=Math.PI*2;
const svg=document.getElementById('sb'), tip=document.getElementById('tip'), crumb=document.getElementById('crumb');
let focus=ROOT, path=[ROOT], metric='usage';  // 'usage' (space%) or 'inode'
function human(b){{const u=['B','KB','MB','GB','TB','PB'];let i=0,n=b||0;
  while(n>=1024&&i<u.length-1){{n/=1024;i++;}}return n.toFixed(n<10?1:0)+u[i];}}
function pct(node){{return node[metric];}}
function colour(p){{if(p==null)return '#5a6472';return p>=85?'#f85149':p>=70?'#d29922':p>=40?'#3fb950':'#2ea043';}}
function polar(r,a){{return [CX+r*Math.cos(a), CY+r*Math.sin(a)];}}
function arcPath(r0,r1,a0,a1){{
  const large=(a1-a0)>Math.PI?1:0;
  const [x0,y0]=polar(r0,a0),[x1,y1]=polar(r1,a0),[x2,y2]=polar(r1,a1),[x3,y3]=polar(r0,a1);
  return `M${{x0}} ${{y0}} L${{x1}} ${{y1}} A${{r1}} ${{r1}} 0 ${{large}} 1 ${{x2}} ${{y2}} `+
         `L${{x3}} ${{y3}} A${{r0}} ${{r0}} 0 ${{large}} 0 ${{x0}} ${{y0}} Z`;
}}
function showTip(e,h){{tip.innerHTML=h; tip.style.opacity=1;
  tip.style.left=Math.min(e.clientX+14,innerWidth-260)+'px'; tip.style.top=(e.clientY+14)+'px';}}
function seg(d,fill,tipHtml,onClick){{
  const p=document.createElementNS('http://www.w3.org/2000/svg','path');
  p.setAttribute('d',d); p.setAttribute('fill',fill); p.setAttribute('class','seg');
  p.onmousemove=e=>showTip(e,tipHtml); p.onmouseleave=()=>tip.style.opacity=0;
  if(onClick){{p.onclick=onClick;}} svg.appendChild(p);
}}
function ring(node, ringIdx, a0, a1, depth){{
  const kids=(node.children||[]).filter(k=>k.value>0);
  const tot=kids.reduce((s,k)=>s+k.value,0)||1;
  let a=a0;
  for(const k of kids){{
    const span=(a1-a0)*k.value/tot, ka1=a+span;
    const r0=R0+ringIdx*RW, r1=r0+RW-6;
    const tipHtml=`<b>${{k.name}}</b><br>capacity ${{human(k.value)}}<br>`+
      `space ${{k.usage==null?'unknown':k.usage+'%'}}<br>`+
      `inodes ${{k.inode==null?'n/a':k.inode+'%'}}`;
    seg(arcPath(r0,r1,a,ka1), colour(pct(k)), tipHtml,
        (k.children&&k.children.length)?()=>zoom(k):null);
    if(depth>0 && k.children && k.children.length){{ ring(k, ringIdx+1, a, ka1, depth-1); }}
    a=ka1;
  }}
}}
function render(){{
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  // centre label = current focus, click to go up
  const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
  c.setAttribute('cx',CX); c.setAttribute('cy',CY); c.setAttribute('r',R0-6);
  c.setAttribute('fill','#1b2027'); c.setAttribute('stroke','#2c333d');
  c.style.cursor = path.length>1?'pointer':'default';
  if(path.length>1) c.onclick=()=>{{path.pop(); focus=path[path.length-1]; render();}};
  svg.appendChild(c);
  const t=document.createElementNS('http://www.w3.org/2000/svg','text');
  t.setAttribute('x',CX); t.setAttribute('y',CY-2); t.setAttribute('text-anchor','middle');
  t.setAttribute('fill','#d7dde5'); t.setAttribute('font-size','14'); t.setAttribute('font-weight','700');
  t.textContent=focus.name; svg.appendChild(t);
  const t2=document.createElementNS('http://www.w3.org/2000/svg','text');
  t2.setAttribute('x',CX); t2.setAttribute('y',CY+16); t2.setAttribute('text-anchor','middle');
  t2.setAttribute('fill','#8b95a3'); t2.setAttribute('font-size','11');
  t2.textContent=human(focus.value); svg.appendChild(t2);
  ring(focus, 0, -Math.PI/2, -Math.PI/2+TAU, 1);
  crumb.innerHTML = path.map((n,i)=>
    i===path.length-1?`<b>${{n.name}}</b>`:`<a data-i="${{i}}">${{n.name}}</a>`).join(' / ');
  crumb.querySelectorAll('a').forEach(a=>a.onclick=()=>{{
    const i=+a.dataset.i; path=path.slice(0,i+1); focus=path[i]; render();}});
  // Reflect the location in the URL so a refresh or a shared link lands
  // where the user was -- they can see WHERE they are, and bookmark it.
  const want='#'+path.slice(1).map(n=>encodeURIComponent(n.name)).join('>');
  if(location.hash!==want && !(location.hash===''&&want==='#')) {{
    history.replaceState(null,'',want);
  }}
}}
function restoreFromHash(){{
  const raw=decodeURIComponent(location.hash.replace(/^#/,''));
  path=[ROOT]; focus=ROOT;
  if(!raw) return;
  for(const name of raw.split('>').map(decodeURIComponent)){{
    const kid=(focus.children||[]).find(c=>c.name===name);
    if(!kid) break;
    path.push(kid); focus=kid;
  }}
}}
window.addEventListener('hashchange',()=>{{restoreFromHash(); render();}});
function zoom(node){{ path.push(node); focus=node; render(); }}
function setMetric(m){{
  metric=m;
  document.getElementById('m-usage').classList.toggle('on', m==='usage');
  document.getElementById('m-inode').classList.toggle('on', m==='inode');
  render();
}}
document.getElementById('m-usage').onclick=()=>setMetric('usage');
document.getElementById('m-inode').onclick=()=>setMetric('inode');
restoreFromHash();
render();
</script>
</body></html>
"""


def build_sunburst_html(snapshot: FleetSnapshot) -> str:
    """Render ``snapshot`` as the interactive capacity sunburst. Pure."""
    root = build_hierarchy(snapshot)
    gen = html.escape(snapshot.generated_at or "")
    note = html.escape(snapshot.note or "")
    gen_line = f"Generated {gen}" + (f" &middot; {note}" if note else "")
    data = json.dumps(root).replace("</", "<\\/")
    return _PAGE.format(gen=gen_line, data=data)

# EOF
