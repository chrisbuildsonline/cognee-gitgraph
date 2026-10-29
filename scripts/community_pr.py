"""Prepares local, never-pushed branches in a clone of topoteretes/cognee-community.

Usage: uv run python scripts/community_pr.py [COMMUNITY_CLONE] [--no-test] [--no-lock] [--out DIR]

For each branch it resets the branch from its base, copies the connector in, adds the README row
and the CI workflow, locks, and makes one signed-off commit. Unless --no-test it then runs the
package's tests and ruff in the clone and writes <out>/<branch>.PR_BODY.md and
<out>/<branch>.tests.png. It never pushes.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).parent.parent
MODULE = "cognee_community_connector_gitgraph"
PKG = Path("packages/connector/gitgraph")
WORKFLOW = Path(".github/workflows/gitgraph_connector_tests.yml")
PACKAGE_NAME = "cognee-community-connector-gitgraph"
ISSUE = "https://github.com/topoteretes/cognee/issues/6329"
DEFAULT_COMM = REPO.parent / "cognee-community-pr"
DEFAULT_OUT = REPO.parent / "cognee-community-pr-bodies"

FIRST_MODULE = ["ci_logs", "git_history", "links", "sample", "source", "symbols"]
SECOND_MODULE = ["blast", "cli", "html"]
FIRST_TESTS = [
    "conftest",
    "test_ci_logs",
    "test_cognee_ingest",
    "test_cognee_roots",
    "test_git_history",
    "test_links",
    "test_sample",
    "test_smoke",
    "test_source",
    "test_symbols",
]
SECOND_TESTS = ["test_blast", "test_cli", "test_demo_acceptance", "test_html"]

DCO = (
    "I affirm that all code in every commit of this pull request conforms to the terms of the "
    "Topoteretes Developer Certificate of Origin."
)


@dataclass
class Branch:
    name: str
    base: str
    modules: list[str]
    tests: list[str]
    with_blast: bool
    commit: str
    row: str
    title: str
    description: str


FIRST = Branch(
    name="gitgraph-connector",
    base="main",
    modules=FIRST_MODULE,
    tests=FIRST_TESTS,
    with_blast=False,
    commit="add gitgraph connector",
    row="Git history, Python symbols and CI failures as Cognee documents",
    title="feat(connector): add GitGraph data-source connector (cognee#6329)",
    description=(
        "Adds `cognee-community-connector-gitgraph`, a dlt source for `cognee.remember` that "
        "loads a git repository as five tables: `commits`, `file_changes`, `symbols` (from "
        "Python's `ast`), `references` and, from JUnit XML, `ci_failures`. `link_rows` adds the "
        "row-to-row edges Cognee does not create for a dlt source.\n\n"
        "It uses the relational-manifest path, so it makes no LLM call and needs no API key. "
        "Repeated syncs use `self_improvement=False`; every table is `replace` with an `id` "
        "primary key, so a second sync adds no new row nodes.\n\n"
        "Proposal: " + ISSUE
    ),
)
SECOND = Branch(
    name="gitgraph-blast-radius",
    base=FIRST.name,
    modules=SECOND_MODULE,
    tests=SECOND_TESTS,
    with_blast=True,
    commit="add blast radius, cli and 3d page",
    row="Git history, Python symbols and CI failures as Cognee documents, with PR blast radius",
    title="feat(connector): add blast radius, CLI and 3D page to GitGraph (cognee#6329)",
    description=(
        "This builds on the gitgraph connector PR (branch `gitgraph-connector`) and should be "
        "merged after it.\n\n"
        "Adds `blast_radius`, a deterministic graph traversal (NetworkX, no LLM) over the "
        "connector's rows: which functions call the code a pull request changes, how many hops "
        "away, which services they live in and which CI failures belong to them. Also adds the "
        "`cognee-gitgraph` console script (`blast` and `demo`, optional `--remember`) and "
        "`--html`, which writes a self-contained 3D page with the 3d-force-graph library (MIT) "
        "inlined from `vendor/`.\n\n"
        "Proposal: " + ISSUE
    ),
)
BRANCHES = [FIRST, SECOND]


def run(cmd: list[str], cwd: Path, check: bool = True) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    output = done.stdout + done.stderr
    if check and done.returncode != 0:
        sys.exit(f"command failed ({done.returncode}): {' '.join(cmd)}\n{output}")
    return done.returncode, output


def git(comm: Path, *args: str) -> str:
    return run(["git", *args], comm)[1]


def copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        shutil.copy2(src, dest)


def render(text: str, with_blast: bool, comment: str) -> str:
    """Keep or drop the blast-radius sections marked in a community file."""
    marked = r"{0}blast-radius:start{1}.*?{0}blast-radius:end{1}"
    if comment == "html":
        start, end = r"\n?<!-- ", r" -->"
    else:
        start, end = r"[ \t]*# ", r"\n"
    pattern = re.compile(marked.format(start, end), re.DOTALL)
    if not with_blast:
        text = pattern.sub("", text)
    return re.sub(rf"{start}blast-radius:(start|end){end}", "", text)


def add_package(comm: Path, branch: Branch) -> None:
    pkg = comm / PKG
    for name in branch.modules:
        copy(REPO / MODULE / f"{name}.py", pkg / MODULE / f"{name}.py")
    if branch.with_blast:
        copy(REPO / MODULE / "__init__.py", pkg / MODULE / "__init__.py")
        copy(REPO / MODULE / "vendor", pkg / MODULE / "vendor")
    else:
        copy(REPO / "community" / "__init__connector.py", pkg / MODULE / "__init__.py")
    for name in branch.tests:
        copy(REPO / "tests" / f"{name}.py", pkg / "tests" / f"{name}.py")
    if not branch.with_blast:
        copy(REPO / "tests" / "fixtures", pkg / "tests" / "fixtures")
        copy(REPO / "examples" / "example.py", pkg / "examples" / "example.py")
    for name, comment in (("README.md", "html"), ("pyproject.toml", "toml")):
        text = (REPO / "community" / name).read_text()
        (pkg / name).write_text(render(text, branch.with_blast, comment))
    workflow = comm / WORKFLOW
    workflow.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "community" / WORKFLOW.name, workflow)


def add_readme_row(comm: Path, branch: Branch) -> None:
    path = comm / "README.md"
    link = f"[{branch.row}](packages/connector/gitgraph/README.md)"
    row = f"| `{PACKAGE_NAME}` | Connector | {link} |"
    lines = path.read_text().splitlines()
    existing = [i for i, line in enumerate(lines) if f"`{PACKAGE_NAME}`" in line]
    if existing:
        lines[existing[0]] = row
    else:
        marker = "`cognee-community-connector-onenote`"
        onenote = [i for i, line in enumerate(lines) if marker in line]
        if not onenote:
            sys.exit("README.md has no onenote connector row to put the row after")
        lines.insert(onenote[0] + 1, row)
    path.write_text("\n".join(lines) + "\n")


def prepare_branch(comm: Path, branch: Branch, lock: bool) -> None:
    git(comm, "checkout", "-B", branch.name, branch.base)
    add_package(comm, branch)
    add_readme_row(comm, branch)
    if lock:
        run(["uv", "lock"], comm / PKG)
    paths = [str(PKG), "README.md", str(WORKFLOW)]
    git(comm, "add", "--", *paths)
    git(comm, "commit", "-s", "-m", branch.commit)
    print(f"committed {branch.name}: {git(comm, 'log', '--oneline', '-1').strip()}")


def tail(text: str, lines: int) -> str:
    rows = text.rstrip().splitlines()
    if "warnings summary" in text:  # keep the pass/fail lines readable
        start = next(i for i, row in enumerate(rows) if "warnings summary" in row)
        docs = (i for i, row in enumerate(rows) if i > start and row.startswith("-- Docs:"))
        end = next(docs, start)
        rows[start : end + 1] = ["(pytest warnings summary omitted)"]
    cut = len(rows) - lines
    return "\n".join(rows if cut <= 0 else [f"... ({cut} earlier lines omitted)", *rows[-lines:]])


def run_checks(comm: Path) -> list[tuple[str, int, str]]:
    pkg = comm / PKG
    commands = [
        ["uv", "sync", "--locked", "--group", "dev"],
        ["uv", "run", "pytest", "tests", "-ra"],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
    ]
    results = []
    for cmd in commands:
        code, output = run(cmd, pkg, check=False)
        results.append((" ".join(cmd), code, output))
        print(f"$ {' '.join(cmd)} -> {code}\n{tail(output, 15)}")
        if code != 0:
            break
    return results


def write_screenshot(path: Path, results: list[tuple[str, int, str]]) -> None:
    from html import escape

    from playwright.sync_api import sync_playwright

    text = "\n\n".join(f"$ {cmd}\n{tail(out, 40)}" for cmd, _, out in results if "sync" not in cmd)
    page = (
        "<body style='margin:0;background:#0d1117'><pre style='color:#e6edf3;"
        "font:13px/1.4 ui-monospace,monospace;padding:16px;white-space:pre-wrap;width:1100px'>"
        f"{escape(text)}</pre></body>"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        shot = browser.new_page(viewport={"width": 1132, "height": 600})
        shot.set_content(page)
        shot.screenshot(path=str(path), full_page=True)
        browser.close()


def write_body(
    out: Path, branch: Branch, comm: Path, results: list[tuple[str, int, str]] | None
) -> None:
    sha = git(comm, "rev-parse", "--short", branch.name).strip()
    parts = [
        branch.title,
        "",
        "## Description",
        branch.description,
        "",
        f"Package: `{PKG}` (commit `{sha}` on local branch `{branch.name}`, "
        f"based on `{branch.base}`).",
        "",
        "## How to test",
        "```bash",
        f"cd {PKG}",
        "uv sync --locked --group dev",
        "uv run pytest tests -ra",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "```",
        "The tests need no API key; the first run downloads Cognee's local models.",
        "",
    ]
    if results:
        parts += ["## Test output", f"Screenshot: `{branch.name}.tests.png` (attach it here).", ""]
        for cmd, code, output in results:
            if "sync" in cmd:
                continue
            parts += [f"`{cmd}` (exit {code})", "```text", tail(output, 60), "```", ""]
    parts += ["## DCO Affirmation", DCO, ""]
    (out / f"{branch.name}.PR_BODY.md").write_text("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("comm", nargs="?", type=Path, default=DEFAULT_COMM)
    parser.add_argument("--no-test", action="store_true", help="skip pytest and ruff in the clone")
    parser.add_argument("--no-lock", action="store_true", help="skip uv lock")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the PR bodies go")
    args = parser.parse_args()

    comm = args.comm.resolve()
    if not (comm / ".git").exists():
        sys.exit(f"{comm} is not a git clone")
    if git(comm, "status", "--porcelain").strip():
        sys.exit(f"{comm} has uncommitted changes; commit or stash them first")
    if not run(["git", "config", "user.email"], comm, check=False)[1].strip():
        sys.exit("git user.name and user.email must be set in the clone for the sign-off")
    git(comm, "rev-parse", "--verify", "main")
    args.out.mkdir(parents=True, exist_ok=True)

    for branch in BRANCHES:
        prepare_branch(comm, branch, lock=not args.no_lock)
        results = None if args.no_test else run_checks(comm)
        if results and any(code != 0 for _, code, _ in results):
            sys.exit(f"checks failed on {branch.name}")
        if results:
            write_screenshot(args.out / f"{branch.name}.tests.png", results)
        write_body(args.out, branch, comm, results)
        print(f"wrote {args.out / (branch.name + '.PR_BODY.md')}")
    git(comm, "checkout", "main")
    print(git(comm, "log", "--oneline", "main..gitgraph-blast-radius"))


if __name__ == "__main__":
    main()
