"""scripts/community_pr.py builds local branches in a clone of cognee-community, offline."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "community_pr.py"
MODULE = "cognee_community_connector_gitgraph"
PKG = "packages/connector/gitgraph"
ROW = "| `cognee-community-connector-gitgraph` | Connector |"

FIRST = "gitgraph-connector"
SECOND = "gitgraph-blast-radius"

FIRST_MODULE = ["__init__", "ci_logs", "git_history", "links", "sample", "source", "symbols"]
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
EXCLUDED = [
    "Dockerfile",
    ".dockerignore",
    "package.json",
    "bun.lock",
    "scripts",
    "assets",
    "tests/test_readme.py",
    "tests/test_demo_video.py",
    "tests/test_html_e2e.py",
]

README = """# Cognee Community

| Package Name | Type | Description |
|---|---|---|
| `cognee-community-connector-onenote` | Connector | [OneNote](packages/connector/onenote/x.md) |
| `cognee-community-pipeline-codify` | Pipeline | Custom codify pipeline package |
"""


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def run_script(comm: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(comm), "--no-test", "--no-lock", "--out", str(out)],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def comm(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("community")
    repo, out = root / "cognee-community", root / "bodies"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test Person")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text(README)
    (repo / "packages" / "connector").mkdir(parents=True)
    (repo / "packages" / "connector" / ".gitkeep").write_text("")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / ".gitkeep").write_text("")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    for _ in range(2):  # the second run must reset to the same result
        done = run_script(repo, out)
        assert done.returncode == 0, done.stdout + done.stderr
    return repo, out


def files(repo: Path, branch: str) -> set[str]:
    return set(git(repo, "ls-tree", "-r", "--name-only", branch, "--", PKG).splitlines())


def show(repo: Path, branch: str, path: str) -> str:
    return git(repo, "show", f"{branch}:{path}")


def test_both_branches_exist(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    branches = git(repo, "branch", "--format=%(refname:short)").split()
    assert {"main", FIRST, SECOND} <= set(branches)
    assert git(repo, "merge-base", "--is-ancestor", FIRST, SECOND) == ""


def test_the_first_branch_has_the_connector_and_not_blast_radius(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    got = files(repo, FIRST)
    expected = {f"{PKG}/{name}" for name in ("README.md", "pyproject.toml", "examples/example.py")}
    expected |= {f"{PKG}/{MODULE}/{name}.py" for name in FIRST_MODULE}
    expected |= {f"{PKG}/tests/{name}.py" for name in FIRST_TESTS}
    assert expected <= got
    assert any(f.startswith(f"{PKG}/tests/fixtures/ci/") for f in got)
    for name in SECOND_MODULE:
        assert f"{PKG}/{MODULE}/{name}.py" not in got
    assert not any("/vendor/" in f for f in got)
    for name in SECOND_TESTS:
        assert f"{PKG}/tests/{name}.py" not in got


def test_the_second_branch_adds_blast_radius_cli_and_html(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    got = files(repo, SECOND)
    assert files(repo, FIRST) - {f"{PKG}/{MODULE}/__init__.py"} <= got
    for name in SECOND_MODULE:
        assert f"{PKG}/{MODULE}/{name}.py" in got
    assert f"{PKG}/{MODULE}/vendor/3d-force-graph.min.js" in got
    assert f"{PKG}/{MODULE}/vendor/LICENSE.txt" in got
    for name in SECOND_TESTS:
        assert f"{PKG}/tests/{name}.py" in got


@pytest.mark.parametrize("branch", [FIRST, SECOND])
def test_hackathon_only_files_are_left_out(comm: tuple[Path, Path], branch: str) -> None:
    repo, _ = comm
    for path in EXCLUDED:
        assert not (files(repo, branch) & {f"{PKG}/{path}"}), path
        assert not any(f.startswith(f"{PKG}/{path}/") for f in files(repo, branch)), path
    assert not (repo / "Dockerfile").exists()


@pytest.mark.parametrize("branch", [FIRST, SECOND])
def test_the_source_files_are_byte_for_byte_the_repo_ones(
    comm: tuple[Path, Path], branch: str
) -> None:
    repo, _ = comm
    for path in files(repo, branch):
        name = path.removeprefix(f"{PKG}/")
        original = REPO / name
        if name == f"{MODULE}/__init__.py" and branch == FIRST:
            continue
        if name.startswith((MODULE, "examples", "tests")):
            assert git(repo, "show", f"{branch}:{path}").encode() == original.read_bytes(), name


def test_the_first_branch_init_does_not_import_blast(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    init = show(repo, FIRST, f"{PKG}/{MODULE}/__init__.py")
    assert "blast" not in init
    assert "gitgraph_source" in init
    assert "blast_radius" in show(repo, SECOND, f"{PKG}/{MODULE}/__init__.py")


def test_the_pyproject_gains_the_cli_only_on_the_second_branch(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    first = show(repo, FIRST, f"{PKG}/pyproject.toml")
    second = show(repo, SECOND, f"{PKG}/pyproject.toml")
    assert "project.scripts" not in first
    assert "networkx" not in first
    assert 'cognee-gitgraph = "cognee_community_connector_gitgraph.cli:main"' in second
    assert "networkx" in second
    for text in (first, second):
        assert "blast-radius:" not in text
        assert "hatchling" in text
        assert "line-length = 100" in text


def test_the_readme_does_not_claim_the_cli_on_the_first_branch(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    first = show(repo, FIRST, f"{PKG}/README.md")
    second = show(repo, SECOND, f"{PKG}/README.md")
    assert "cognee-gitgraph" not in first
    assert "cognee-gitgraph blast" in second
    assert "self_improvement=False" in first
    for text in (first, second):
        assert "blast-radius:" not in text
        assert "<!--" not in text
        assert "\n\n\nasync def main" in text  # ruff formats the example, so keep its spacing
        assert "\n\n\n\n" not in text
        assert "## Tests\n" in text
        assert ".\n\n\n## " not in text


@pytest.mark.parametrize("branch", [FIRST, SECOND])
def test_the_package_table_row_is_added_once_after_onenote(
    comm: tuple[Path, Path], branch: str
) -> None:
    repo, _ = comm
    readme = show(repo, branch, "README.md")
    assert readme.count(ROW) == 1
    lines = readme.splitlines()
    onenote = next(i for i, line in enumerate(lines) if "connector-onenote" in line)
    assert lines[onenote + 1].startswith(ROW)
    assert "(packages/connector/gitgraph/README.md)" in lines[onenote + 1]
    assert readme.count("cognee-community-pipeline-codify") == 1


@pytest.mark.parametrize("branch", [FIRST, SECOND])
def test_the_ci_workflow_is_added(comm: tuple[Path, Path], branch: str) -> None:
    repo, _ = comm
    workflow = show(repo, branch, ".github/workflows/gitgraph_connector_tests.yml")
    assert "working-directory: packages/connector/gitgraph" in workflow
    steps = ("uv sync --locked", "pytest tests", "ruff check", "ruff format --check", "uv build")
    for step in steps:
        assert step in workflow


def test_every_commit_is_signed_off_and_has_no_ai_attribution(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    commits = git(repo, "rev-list", "main..gitgraph-blast-radius").split()
    assert len(commits) == 2
    for sha in commits:
        message = git(repo, "show", "-s", "--format=%B", sha)
        assert "Signed-off-by: Test Person <test@example.com>" in message
        for banned in ("co-authored-by", "generated with", "claude", "anthropic"):
            assert banned not in message.lower()
        subject = message.splitlines()[0]
        assert subject == subject.lower() and len(subject) < 50


def test_commits_touch_only_the_package_readme_and_workflow(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    for sha in git(repo, "rev-list", "main..gitgraph-blast-radius").split():
        for path in git(repo, "show", "--name-only", "--format=", sha).split():
            assert path.startswith(PKG + "/") or path in (
                "README.md",
                ".github/workflows/gitgraph_connector_tests.yml",
            ), path


def test_the_second_commit_stacks_on_the_first(comm: tuple[Path, Path]) -> None:
    repo, _ = comm
    assert git(repo, "rev-list", "--count", f"main..{FIRST}").strip() == "1"
    assert git(repo, "rev-list", "--count", f"{FIRST}..{SECOND}").strip() == "1"


@pytest.mark.parametrize("branch", [FIRST, SECOND])
def test_the_pr_body_has_title_issue_link_and_dco(comm: tuple[Path, Path], branch: str) -> None:
    _, out = comm
    body = (out / f"{branch}.PR_BODY.md").read_text()
    first_line = body.splitlines()[0]
    assert first_line.startswith("feat(connector): ")
    assert first_line.endswith("(cognee#6329)")
    assert "https://github.com/topoteretes/cognee/issues/6329" in body
    assert "## Description" in body
    assert "## How to test" in body
    assert "## DCO Affirmation" in body
    assert "Topoteretes Developer Certificate of Origin" in body
    assert "co-authored-by" not in body.lower()


def test_the_second_pr_body_says_it_builds_on_the_first(comm: tuple[Path, Path]) -> None:
    _, out = comm
    assert "builds on" in (out / f"{SECOND}.PR_BODY.md").read_text()
    assert "builds on" not in (out / f"{FIRST}.PR_BODY.md").read_text()


def test_a_dirty_community_clone_is_refused(comm: tuple[Path, Path], tmp_path: Path) -> None:
    repo, _ = comm
    git(repo, "checkout", "main")
    (repo / "stray.txt").write_text("x")
    done = run_script(repo, tmp_path)
    (repo / "stray.txt").unlink()
    assert done.returncode != 0
    assert "uncommitted" in done.stdout + done.stderr
