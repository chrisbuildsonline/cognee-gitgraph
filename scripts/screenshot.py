"""Writes assets/blast-3d.png: the 3D page for the sample repo, after the layout has spread."""

import contextlib
import io
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from cognee_community_connector_gitgraph import build_sample_repo
from cognee_community_connector_gitgraph.cli import main

OUT = Path(__file__).parent.parent / "assets" / "blast-3d.png"
CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


def run() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_sample_repo(Path(tmp) / "repo")
        html = Path(tmp) / "blast.html"
        argv = ["blast", "--repo", str(repo.path), "--base", repo.base, "--head", repo.head]
        with contextlib.redirect_stdout(io.StringIO()):
            main([*argv, "--html", str(html)])
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            page.goto(html.as_uri())
            page.wait_for_function("window.graph && window.graph.graphData().nodes.length > 0")
            page.wait_for_timeout(3000)
            page.evaluate("() => { window.graph.zoomToFit(400, 40); }")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(OUT))
            browser.close()


if __name__ == "__main__":
    run()
