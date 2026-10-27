"""The README matches the code: the demo output, the bun scripts and the blast flags."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_cli import _keyless

from cognee_community_connector_gitgraph.cli import _parser

ROOT = Path(__file__).parent.parent
README = (ROOT / "README.md").read_text()


def _blast_flags() -> set[str]:
    sub = next(a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction))
    blast = sub.choices["blast"]
    return {o for a in blast._actions for o in a.option_strings} - {"-h", "--help"}


@pytest.mark.integration
def test_quick_start_output_is_the_real_demo_output() -> None:
    """Given the README's text block, when the demo runs keyless, then stdout is exactly it."""
    quick_start = README.split("## Quick start", 1)[1]
    block = re.search(r"```text\n(.*?)\n```", quick_start, re.DOTALL)
    assert block
    code = "import sys; from cognee_community_connector_gitgraph.cli import main; sys.exit(main())"
    done = subprocess.run(
        [sys.executable, "-c", code, "demo"],
        capture_output=True,
        text=True,
        check=False,
        env=_keyless(),
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.rstrip("\n") == block.group(1)


def test_every_bun_script_in_the_readme_exists() -> None:
    """Given `bun run <name>` in the README, then package.json has that script."""
    scripts = json.loads((ROOT / "package.json").read_text())["scripts"]
    named = set(re.findall(r"bun run ([\w:]+)", README))
    assert named
    assert named <= set(scripts), named - set(scripts)


def test_the_blast_usage_line_lists_exactly_the_parser_flags() -> None:
    """Given the README usage line, then its flags are the blast parser's flags, no more or less."""
    usage = next(line for line in README.splitlines() if line.startswith("cognee-gitgraph blast"))
    assert set(re.findall(r"--[a-z]+", usage)) == _blast_flags()
