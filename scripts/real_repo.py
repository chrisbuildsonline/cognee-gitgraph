"""Writes tests/fixtures/real_repo/blast.json: the blast radius of a real commit in a real repo.

Clones karpathy/nanochat (MIT) into out/ and runs ``cognee-gitgraph blast --json`` on a pinned
base...head pair through the CLI's own ``main``. HEAD is "fix: safe DDP cleanup (check
initialized PG, not just env) (#256)", which changed ``get_dist_info``, ``compute_init`` and
``compute_cleanup`` in nanochat/common.py; BASE is its parent. Prints the text report.
"""

import contextlib
import io
import json
import subprocess
from pathlib import Path

from cognee_community_connector_gitgraph.cli import _report, main

ROOT = Path(__file__).parent.parent
URL = "https://github.com/karpathy/nanochat.git"
BASE = "91d76cc690ac35a253651e886a4f0b34d745a232"
HEAD = "2f2d7ab80cd07a8b5fab9feebcb185fd3ca37339"
CLONE = ROOT / "out" / "real_repo" / "nanochat"
FIXTURE = ROOT / "tests" / "fixtures" / "real_repo" / "blast.json"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True)


def run() -> None:
    if not (CLONE / ".git").exists():
        _git("clone", "--quiet", URL, str(CLONE))
    _git("-C", str(CLONE), "-c", "advice.detachedHead=false", "checkout", "--quiet", HEAD)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["blast", "--repo", str(CLONE), "--base", BASE, "--head", HEAD, "--json"])
    if code:
        raise SystemExit(code)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(out.getvalue(), encoding="utf-8")
    print(_report(json.loads(out.getvalue())))


if __name__ == "__main__":
    run()
