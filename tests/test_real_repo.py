"""The README's real-repo report is the CLI's own report of the recorded blast. Offline."""

import json
import re
from pathlib import Path
from typing import Any

from cognee_community_connector_gitgraph.cli import _report

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "real_repo" / "blast.json"
SCRIPT = ROOT / "scripts" / "real_repo.py"
REPO = "https://github.com/karpathy/nanochat"
BASE = "91d76cc690ac35a253651e886a4f0b34d745a232"
HEAD = "2f2d7ab80cd07a8b5fab9feebcb185fd3ca37339"  # fix: safe DDP cleanup (#256)
FIXED = {
    f"nanochat/common.py::{name}" for name in ("compute_cleanup", "compute_init", "get_dist_info")
}


def _recorded() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(FIXTURE.read_text())
    return result


def _section() -> str:
    readme = (ROOT / "README.md").read_text()
    assert "\n## On a real repo\n" in readme
    return readme.split("\n## On a real repo\n", 1)[1].split("\n## ", 1)[0]


def test_the_readme_block_is_the_report_of_the_recorded_blast() -> None:
    """Given the recorded JSON, when the CLI renders it, then it is the README's block exactly."""
    block = re.search(r"```text\n(.*?)\n```", _section(), re.DOTALL)
    assert block
    assert block.group(1) == _report(_recorded())


def test_the_fixture_is_the_pinned_commit_pair_from_the_script() -> None:
    """Given the fixture, then it is the blast of the full SHAs the script pins, from the rows."""
    result = _recorded()
    assert (result["base"], result["head"]) == (BASE, HEAD)
    assert result["computed_from"] == "rows"
    script = SCRIPT.read_text()
    assert f"{REPO}.git" in script
    assert BASE in script
    assert HEAD in script


def test_the_readme_links_both_commits_and_the_command() -> None:
    """Given the README section, then it links the base and head commits and says how to rerun."""
    section = _section()
    assert f"{REPO}/commit/{BASE}" in section
    assert f"{REPO}/commit/{HEAD}" in section
    assert "bun run real-repo" in section


def test_the_fixed_functions_changed_and_their_callers_are_at_risk() -> None:
    """Given the fixture, then the functions the commit fixed are changed and have callers."""
    result = _recorded()
    assert result["changed_files"] == ["nanochat/common.py"]
    assert set(result["changed_symbols"]) >= FIXED
    hops = {s["id"]: s["hops"] for s in result["symbols"] if s["path"][0] in FIXED}
    assert hops["nanochat/dataloader.py::tokenizing_distributed_data_loader_with_state"] == 1
    assert hops["nanochat/dataloader.py::tokenizing_distributed_data_loader"] == 2
    assert {s["name"] for s in result["services"]} == {"dev", "scripts", "tasks", "tests"}
