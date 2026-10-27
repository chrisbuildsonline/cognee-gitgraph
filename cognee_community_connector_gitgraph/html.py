"""The blast radius as one self-contained 3D page.

``render_html`` is pure: it takes the same table-name -> rows mapping and result as
``blast_radius`` and returns a page with the vendored 3d-force-graph bundle (which carries
three.js) inlined, so opening it makes no network request.
"""

import json
from collections.abc import Iterable, Mapping
from html import escape
from pathlib import Path
from typing import Any

VENDOR = Path(__file__).parent / "vendor"

_COLORS = {"changed": "#ef4444", "at-risk": "#f59e0b", "safe": "#64748b"}

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Blast radius of {title}</title>
<style>
html, body {{ margin: 0; height: 100%; background: #0b1020; color: #e2e8f0;
  font-family: system-ui, sans-serif; overflow: hidden; }}
#graph {{ position: absolute; inset: 0; }}
#labels {{ position: absolute; inset: 0; pointer-events: none; overflow: hidden; }}
#labels div {{ position: absolute; transform: translate(-50%, 16px); white-space: nowrap;
  padding: 2px 7px; font-size: 12px; background: rgba(11, 16, 32, 0.8); border: 1px solid;
  border-radius: 4px; }}
#panel {{ position: absolute; top: 16px; left: 16px; z-index: 1; max-width: 420px;
  padding: 14px 18px; background: rgba(15, 23, 42, 0.88); border: 1px solid #334155;
  border-radius: 8px; font-size: 14px; line-height: 1.5; }}
#panel h1 {{ margin: 0 0 8px; font-size: 16px; }}
#panel h2 {{ margin: 12px 0 4px; font-size: 13px; color: #94a3b8; text-transform: uppercase; }}
#panel ul {{ margin: 0; padding-left: 18px; }}
#panel .legend {{ list-style: none; padding: 0; }}
#panel .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: 8px; }}
</style>
</head>
<body>
<aside id="panel">
<h1>Blast radius of {title}</h1>
<h2>At-risk services</h2>
<ul id="services">
{services}
</ul>
<h2>Legend</h2>
<ul class="legend">
{legend}
</ul>
</aside>
<div id="graph"></div>
<div id="labels"></div>
<script>{library}</script>
<script id="graph-data" type="application/json">{data}</script>
<script>
const data = JSON.parse(document.getElementById("graph-data").textContent);
const colors = {colors};
// The tooltip is HTML and ids come from file paths: escape every part of it.
const entities = {{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => entities[c]);
window.graph = ForceGraph3D()(document.getElementById("graph"))
  .backgroundColor("#0b1020")
  .graphData(data)
  .nodeColor((n) => colors[n.status])
  .numDimensions(2) // a flat layout, seen at an angle, keeps the labels from stacking up
  .nodeRelSize(7)
  .nodeResolution(24)
  .nodeVal((n) => (n.status === "safe" ? 3 : 8))
  .nodeLabel((n) => esc(n.label) + "<br>service: " + esc(n.service))
  .linkColor(() => "#94a3b8")
  .linkOpacity(0.6)
  .linkWidth(1.5)
  .linkDirectionalArrowLength(7)
  .linkDirectionalArrowRelPos(0.9);
// Always-visible labels: one div per node, moved to the node's screen position every frame.
const labels = document.getElementById("labels");
const tags = data.nodes.map((n) => {{
  const tag = document.createElement("div");
  tag.textContent = n.short;
  tag.title = n.id;
  tag.style.color = colors[n.status];
  tag.style.borderColor = colors[n.status];
  tag.style.display = "none";
  labels.appendChild(tag);
  return tag;
}});
function placeLabels() {{
  data.nodes.forEach((n, i) => {{
    if (n.x === undefined) return;
    const at = window.graph.graph2ScreenCoords(n.x, n.y, n.z);
    tags[i].style.left = at.x + "px";
    tags[i].style.top = at.y + "px";
    tags[i].style.display = "block";
  }});
  requestAnimationFrame(placeLabels);
}}
requestAnimationFrame(placeLabels);
window.graph.cameraPosition({{ x: 60, y: 90, z: 320 }});
window.graph.onEngineStop(() => window.graph.zoomToFit(400, 40));
window.graph.d3Force("link").distance(110);
window.graph.d3Force("charge").strength(-160);
</script>
</body>
</html>
"""


def _hops(count: int) -> str:
    return f"{count} hop" if count == 1 else f"{count} hops"


def _service(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "."


def graph_data(
    tables: Mapping[str, Iterable[Mapping[str, Any]]], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Nodes (one per symbol, with status and hops) and caller -> callee links."""
    changed = set(result["changed_symbols"])
    hops = {s["id"]: s["hops"] for s in result["symbols"]}
    nodes = []
    for row in tables.get("symbols", []):
        sid = row["id"]
        service = _service(row["file"])
        node: dict[str, Any] = {"id": sid, "service": service, "label": sid}
        # the short, always-visible text: "charge · billing · 1 hop"
        short = f"{sid.rpartition('::')[2]} · {service} · "
        if sid in changed:
            node |= {"status": "changed", "short": short + "changed"}
        elif sid in hops:
            node |= {
                "status": "at-risk",
                "hops": hops[sid],
                "label": f"{sid} ({_hops(hops[sid])})",
                "short": short + _hops(hops[sid]),
            }
        else:
            node |= {"status": "safe", "short": short + "safe"}
        nodes.append(node)
    ids = {n["id"] for n in nodes}
    # Module-level imports (no owner symbol) are file -> symbol edges in blast.py; the page draws
    # symbols only, so they have no end to attach to here.
    links = sorted(
        {
            (ref["from_symbol"], ref["to_symbol"])
            for ref in tables.get("references", [])
            if ref.get("from_symbol") in ids
            and ref.get("to_symbol") in ids
            and ref["from_symbol"] != ref["to_symbol"]
        }
    )
    return {"nodes": nodes, "links": [{"source": s, "target": t} for s, t in links]}


def render_html(
    tables: Mapping[str, Iterable[Mapping[str, Any]]], result: Mapping[str, Any]
) -> str:
    """The page for one blast result; ``window.graph`` is the ForceGraph3D instance."""
    tables = {name: list(rows) for name, rows in tables.items()}
    services = [
        f"<li>{escape(s['name'])}: {_hops(s['hops'])}</li>" for s in result["services"]
    ] or ["<li>none</li>"]
    legend = [
        f'<li><span class="dot" style="background: {_COLORS[status]}"></span>{text}</li>'
        for status, text in (
            ("changed", "changed symbol"),
            ("at-risk", "at risk (calls the change)"),
            ("safe", "safe"),
        )
    ]
    # A "<" in the data could close the script element or open a comment; < is the same
    # character to a JSON parser.
    data = json.dumps(graph_data(tables, result)).replace("<", "\\u003c")
    return _PAGE.format(
        title=escape(f"{result['base']}...{result['head']}"),
        services="\n".join(services),
        legend="\n".join(legend),
        library=(VENDOR / "3d-force-graph.min.js").read_text(encoding="utf-8"),
        data=data,
        colors=json.dumps(_COLORS),
    )
