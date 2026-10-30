"""The 3D page in headless chromium: no network, no console errors, the graph and the panel."""

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, Request, Route, sync_playwright
from test_cli import _args

from cognee_community_connector_gitgraph import build_sample_repo
from cognee_community_connector_gitgraph.cli import main
from cognee_community_connector_gitgraph.html import render_html

# Headless chromium has no GPU: software WebGL through SwiftShader.
CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


@pytest.mark.e2e
def test_hostile_symbol_names_cannot_run_script_in_the_tooltip(tmp_path: Path) -> None:
    """Given a name like an img tag, when its tooltip is built and shown, then no script runs."""
    evil = '<img src=x onerror="window.__xss=1">'
    sid = f"svc/mod.py::{evil}"
    tables = {"symbols": [{"id": sid, "kind": "function", "name": evil, "file": "svc/mod.py"}]}
    result = {
        "changed_symbols": [sid],
        "symbols": [],
        "services": [],
        "base": "main",
        "head": "feature",
    }
    page_file = tmp_path / "evil.html"
    page_file.write_text(render_html(tables, result), encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page()
        page.goto(page_file.as_uri())
        page.wait_for_function("window.graph && window.graph.graphData().nodes.length === 1")
        tooltip = page.evaluate("() => window.graph.nodeLabel()(window.graph.graphData().nodes[0])")
        # the library puts this string into the DOM as HTML, so do the same
        page.evaluate("(html) => { document.body.insertAdjacentHTML('beforeend', html); }", tooltip)
        page.wait_for_timeout(300)
        injected = page.evaluate("document.querySelectorAll('body img').length")
        flag = page.evaluate("typeof window.__xss")
        label = page.inner_text("#labels > div")
        browser.close()

    assert "&lt;img" in tooltip
    assert "<img" not in tooltip
    assert injected == 0
    assert flag == "undefined"
    assert evil in label  # the overlay label shows it as plain text


@pytest.mark.e2e
def test_blast_page_renders_the_graph_offline(tmp_path: Path) -> None:
    """Given a blast page, when opened with the network blocked, then the graph and panel show."""
    repo = build_sample_repo(tmp_path / "repo").path
    page_file = tmp_path / "blast.html"
    assert main(_args(repo, "--html", str(page_file))) == 0

    blocked: list[str] = []
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1280, "height": 720})

        def only_files(route: Route, request: Request) -> None:
            if request.url.startswith("file:"):
                route.continue_()
            else:
                blocked.append(request.url)
                route.abort()

        page.route("**/*", only_files)
        page.on("console", lambda m: errors.extend([m.text] if m.type == "error" else []))
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(page_file.as_uri())
        page.wait_for_function("window.graph && window.graph.graphData().nodes.length === 8")
        page.wait_for_selector("canvas")
        page.wait_for_timeout(1500)
        panel = page.inner_text("#panel")
        statuses = page.evaluate(
            "Object.fromEntries(window.graph.graphData().nodes.map(n => [n.id, n.status]))"
        )
        labels = page.eval_on_selector_all(
            "#labels > div", "els => els.map(e => [e.textContent, e.style.display])"
        )
        page.screenshot(path=str(tmp_path / "blast.png"))
        browser.close()

    texts = [text for text, _ in labels]
    assert len(labels) == 8
    assert all(display != "none" for _, display in labels)  # every node got screen coordinates
    assert [t for t in texts if "1 hop" in t and "billing" in t] == ["charge · billing · 1 hop"]
    assert "verify_token · shared · changed" in texts
    assert "search · search · safe" in texts
    assert not any("search" in t and "hop" in t for t in texts)

    assert blocked == []
    assert errors == []
    for line in ("billing: 1 hop", "orders: 2 hops", "notifications: 4 hops"):
        assert line in panel
    assert "search" not in panel
    assert "main...feature/rotate-tokens" in panel
    assert statuses["shared/auth.py::verify_token"] == "changed"
    assert statuses["search/index.py::search"] == "safe"
    assert (tmp_path / "blast.png").stat().st_size > 10_000


# Every label as the user sees it: text, status, box, whether it is shown at all, and where its
# node is on the page.
LABEL_BOXES = """() => {
  const g = window.graph, nodes = g.graphData().nodes;
  const canvas = document.querySelector("canvas").getBoundingClientRect();
  return [...document.querySelectorAll("#labels > div")].map((e, i) => {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    const shown = s.display !== "none" && s.visibility !== "hidden" && +s.opacity > 0;
    const n = nodes[i], at = g.graph2ScreenCoords(n.x, n.y, n.z);
    return { text: e.textContent, status: n.status, shown: shown && r.width > 0,
             box: [r.left, r.top, r.right, r.bottom],
             node: [canvas.left + at.x, canvas.top + at.y] };
  });
}"""
# The area the graph draws in, as [left, top, right, bottom] on the page.
STAGE = """() => {
  const r = document.querySelector("canvas").getBoundingClientRect();
  return [r.left, r.top, r.right, r.bottom];
}"""
# The summary card, as [left, top, right, bottom] on the page.
PANEL = """() => {
  const r = document.getElementById("panel").getBoundingClientRect();
  return [r.left, r.top, r.right, r.bottom];
}"""


def _under_panel(labels: list[dict[str, Any]], panel: list[float]) -> list[str]:
    """Blast labels and nodes the summary card covers (a node counts with a 10px radius)."""
    covered = [
        n["text"]
        for n in labels
        if n["status"] != "safe"
        and n["shown"]
        and n["box"][0] < panel[2]
        and panel[0] < n["box"][2]
        and n["box"][1] < panel[3]
        and panel[1] < n["box"][3]
    ]
    return covered + [f"node of {n['text']}" for n in labels if _gap(panel, n["node"]) < 10]


def _gap(box: list[float], point: list[float]) -> float:
    """Distance from a point to the nearest edge of a box (0 inside it)."""
    dx = max(box[0] - point[0], 0.0, point[0] - box[2])
    dy = max(box[1] - point[1], 0.0, point[1] - box[3])
    return float((dx * dx + dy * dy) ** 0.5)


def _misread(labels: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Labels that sit nearer another node than their own: (label, the node it seems to name)."""
    return [
        (n["text"], other["text"])
        for n in labels
        if n["shown"]
        for other in labels
        if other is not n and _gap(n["box"], other["node"]) < _gap(n["box"], n["node"])
    ]


def _span(labels: list[dict[str, Any]], stage: list[float]) -> tuple[float, float]:
    """How much of the stage width and height the graph covers, its labels included."""
    xs = [x for n in labels for x in [n["node"][0], *(n["box"][::2] if n["shown"] else [])]]
    ys = [y for n in labels for y in [n["node"][1], *(n["box"][1::2] if n["shown"] else [])]]
    return (
        (max(xs) - min(xs)) / (stage[2] - stage[0]),
        (max(ys) - min(ys)) / (stage[3] - stage[1]),
    )


TILT = """() => {
  const cam = window.graph.camera().position, at = window.graph.controls().target;
  const d = cam.distanceTo(at);
  window.graph.cameraPosition({ x: at.x - 0.45 * d, y: at.y - 0.55 * d, z: at.z + 0.7 * d }, at, 0);
}"""


def _settled_labels(page: Page) -> list[dict[str, Any]]:
    """Label boxes once they stop moving (layout and camera both done)."""
    last: list[Any] = []
    still = 0
    for _ in range(120):
        page.wait_for_timeout(250)
        now = page.evaluate(LABEL_BOXES)
        still = still + 1 if now == last and any(n["shown"] for n in now) else 0
        if still == 2:
            return list(now)
        last = now
    raise AssertionError(f"labels never stopped moving: {last}")


def _overlaps(labels: list[dict[str, Any]]) -> list[tuple[str, str]]:
    shown = [n for n in labels if n["shown"]]
    return [
        (a["text"], b["text"])
        for i, a in enumerate(shown)
        for b in shown[i + 1 :]
        if a["box"][0] < b["box"][2]
        and b["box"][0] < a["box"][2]
        and a["box"][1] < b["box"][3]
        and b["box"][1] < a["box"][3]
    ]


@pytest.mark.e2e
@pytest.mark.parametrize("width,height", [(390, 844), (1280, 800), (1920, 1080)])
def test_labels_never_overlap_and_the_blast_labels_stay_visible(
    tmp_path: Path, width: int, height: int
) -> None:
    """Given the sample page, when the layout settles or the camera turns, labels do not overlap."""
    repo = build_sample_repo(tmp_path / "repo").path
    page_file = tmp_path / "blast.html"
    assert main(_args(repo, "--html", str(page_file))) == 0

    errors: list[str] = []
    views: dict[str, list[dict[str, Any]]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("console", lambda m: errors.extend([m.text] if m.type == "error" else []))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(page_file.as_uri())
        page.wait_for_function("window.graph && window.graph.graphData().nodes.length === 8")
        views["as opened"] = _settled_labels(page)
        stage = page.evaluate(STAGE)
        panel = page.evaluate(PANEL)
        # the library's own (loose) fit: the zoom where fixed-size labels crowd the most
        page.evaluate("() => window.graph.zoomToFit(0, 100)")
        views["fit to screen"] = _settled_labels(page)
        # the user tilts the graph (same distance, so it stays in view)
        page.evaluate(TILT)
        views["tilted"] = _settled_labels(page)
        page.screenshot(path=str(tmp_path / "labels.png"))
        browser.close()

    assert errors == []
    problems = {}
    for view, labels in views.items():
        blast = [n for n in labels if n["status"] in ("changed", "at-risk")]
        off_stage = [
            n["text"]
            for n in blast
            if n["box"][0] < stage[0]
            or n["box"][1] < stage[1]
            or n["box"][2] > stage[2]
            or n["box"][3] > stage[3]
        ]
        found = {
            "overlapping": _overlaps(labels),
            # changed and at-risk labels always keep their place, on the stage
            "hidden": [n["text"] for n in blast if not n["shown"]],
            "off stage": off_stage,
            # each label reads as naming its own node, not a neighbour
            "nearer another node": _misread(labels),
            # the summary card never hides the graph, at any screen size
            "under the panel": _under_panel(labels, panel),
        }
        # only verify_token changed; make_token is just a safe neighbour
        assert len(labels) == 8 and len(blast) == 6
        problems[view] = {k: v for k, v in found.items() if v}
    # as opened, the graph fills the stage rather than floating in the middle of it
    wide, tall = _span(views["as opened"], stage)
    if max(wide, tall) < 0.65:
        problems["as opened"]["fills too little (width, height)"] = [round(wide, 2), round(tall, 2)]
    assert problems == {"as opened": {}, "fit to screen": {}, "tilted": {}}
