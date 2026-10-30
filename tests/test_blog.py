"""The blog post matches the code: the demo output, the hop counts it states and its links."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_cli import _keyless

ROOT = Path(__file__).parent.parent
POST = ROOT / "blog" / "post.md"
ISSUE = "https://github.com/topoteretes/cognee/issues/6329"
REPO_URL = "https://github.com/chrisbuildsonline/cognee-gitgraph"
SERVICES = ("billing", "orders", "notifications", "search")
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_HOP_ITEM = re.compile(
    r"\b(?P<service>" + "|".join(SERVICES) + r")\b"
    r"|\b(?P<count>\d+|" + "|".join(NUMBERS) + r")[ -]hops?\b",
    re.IGNORECASE,
)


def _post() -> str:
    return POST.read_text()


def _prose() -> str:
    """The post without its fenced code blocks."""
    return re.sub(r"```.*?```", "", _post(), flags=re.DOTALL)


def _demo_block() -> str:
    block = re.search(r"```text\n(Blast radius of .*?)\n```", _post(), re.DOTALL)
    assert block, "the post has no ```text block with the demo output"
    return block.group(1)


def _service_hops(report: str) -> dict[str, int]:
    """Service name -> hops, from the "At-risk services:" lines of the text report."""
    lines = re.findall(r"^  (\w+)  (\d+) hops?  ", report, re.MULTILINE)
    return {name: int(hops) for name, hops in lines}


def _stated_hops(prose: str) -> list[tuple[str | None, int]]:
    """Each hop count in the prose, with the service named right before it (or None)."""
    stated: list[tuple[str | None, int]] = []
    service: str | None = None
    for item in _HOP_ITEM.finditer(prose):
        if item.group("service"):
            service = item.group("service").lower()
            continue
        count = item.group("count").lower()
        stated.append((service, int(NUMBERS.get(count, count))))
        service = None
    return stated


@pytest.fixture(scope="module")
def demo_stdout() -> str:
    code = "import sys; from cognee_community_connector_gitgraph.cli import main; sys.exit(main())"
    done = subprocess.run(
        [sys.executable, "-c", code, "demo"],
        capture_output=True,
        text=True,
        check=False,
        env=_keyless(),
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.rstrip("\n")


def test_the_post_exists() -> None:
    """Given the repo, then blog/post.md is there."""
    assert POST.is_file()


@pytest.mark.integration
def test_the_demo_block_is_the_real_demo_output(demo_stdout: str) -> None:
    """Given the post's demo block, when the demo runs keyless, then stdout is exactly it."""
    assert _demo_block() == demo_stdout


@pytest.mark.integration
def test_every_hop_count_in_the_prose_matches_the_demo(demo_stdout: str) -> None:
    """Given a hop count stated after a service name, then it is that service's demo hop count."""
    real = _service_hops(demo_stdout)
    assert real == {"billing": 1, "notifications": 4, "orders": 2}
    stated = _stated_hops(_prose())
    for service, hops in stated:
        if service is None:
            assert hops in set(real.values()), f"{hops} hops is no service's hop count"
        else:
            assert real.get(service) == hops, f"the post says {service} is {hops} hops away"
    assert {s for s, _ in stated if s} >= set(real), "a service's hop count is never stated"


@pytest.mark.integration
def test_the_post_calls_search_safe_and_the_demo_agrees(demo_stdout: str) -> None:
    """Given the post says search is safe, then the demo puts search in no at-risk service."""
    assert re.search(r"\bsearch\b[^.]*\bsafe\b", _prose(), re.IGNORECASE)
    assert "search" not in _service_hops(demo_stdout)


def test_the_post_links_the_issue_and_the_repo() -> None:
    """Given the post, then it links the Mergetober proposal issue and the source repo."""
    assert ISSUE in _post()
    assert REPO_URL in _post()


@pytest.mark.parametrize("word", ["todo", "tbd", "coming soon"])
def test_the_post_has_no_placeholder_text(word: str) -> None:
    """Given the post, then it has no TODO, TBD or "coming soon" in any case."""
    assert word not in _post().lower()


def test_every_bun_script_in_the_post_exists() -> None:
    """Given `bun run <name>` in the post, then package.json has that script."""
    scripts = json.loads((ROOT / "package.json").read_text())["scripts"]
    named = set(re.findall(r"bun run ([\w:]+)", _post()))
    assert named
    assert named <= set(scripts), named - set(scripts)


def test_every_image_in_the_post_exists() -> None:
    """Given a relative image link in the post, then the file is in the repo."""
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", _post()):
        if "://" not in target:
            assert (POST.parent / target).resolve().is_file(), target


def test_the_post_is_short() -> None:
    """Given the post's prose, then it is a short read: 600 to 1100 words."""
    words = len(_prose().split())
    assert 600 <= words <= 1100, words


def test_the_readme_links_the_post() -> None:
    """Given the README, then it links blog/post.md."""
    assert "](blog/post.md)" in (ROOT / "README.md").read_text()
