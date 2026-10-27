"""The 3D page in headless chromium: no network, no console errors, the graph and the panel."""

from pathlib import Path

import pytest
from playwright.sync_api import Request, Route, sync_playwright
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
