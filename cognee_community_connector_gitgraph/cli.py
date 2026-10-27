"""``cognee-gitgraph``: the blast radius of a branch, and a one-command demo."""

import argparse
import asyncio
import contextlib
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from git import GitCommandError, Repo

from .blast import blast_radius
from .ci_logs import parse_ci_logs
from .git_history import extract_commits
from .sample import build_sample_repo
from .symbols import extract_symbols

# The demo ships its CI logs as strings: the wheel carries no fixture files.
_DEMO_CI = {
    "billing-tests.xml": (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="billing.test_invoice" name="test_charge_valid_token"/>'
        '<testcase classname="billing.test_invoice" name="test_charge_with_max_age">'
        "<error message=\"TypeError: charge() got an unexpected keyword argument 'max_age'\">"
        "TypeError</error></testcase></testsuite></testsuites>"
    ),
    "orders-tests.xml": (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="orders.test_checkout" name="test_checkout_returns_cart"'
        ' file="orders/test_checkout.py" line="5"/>'
        '<testcase classname="orders.test_checkout" name="test_checkout_rejects_bad_token"'
        ' file="orders/test_checkout.py" line="11">'
        '<failure message="assert False is True">AssertionError</failure>'
        "</testcase></testsuite></testsuites>"
    ),
}


def _hops(count: int) -> str:
    return f"{count} hop" if count == 1 else f"{count} hops"


def _changed_paths(repo: Path, base: str, head: str) -> list[str]:
    try:
        out = Repo(repo).git.diff("--name-only", "-z", f"{base}...{head}")
    except GitCommandError as exc:
        raise ValueError(f"cannot diff {base}...{head}") from exc
    return [path for path in out.split("\0") if path]


def _blast(args: argparse.Namespace) -> dict[str, Any]:
    refs = [args.base, args.head]
    history = extract_commits(args.repo, refs)  # first, so a bad repo or ref fails here
    code = extract_symbols(args.repo, args.head)
    tables = {
        "commits": history["commits"],
        "file_changes": history["file_changes"],
        "symbols": code["symbols"],
        "references": code["references"],
    }
    if args.ci:
        tables["ci_failures"] = parse_ci_logs(args.ci)["ci_failures"]
    changed = _changed_paths(args.repo, args.base, args.head)
    result = blast_radius(tables, changed, max_hops=args.hops)
    result |= {"base": args.base, "head": args.head}
    if args.remember:
        sha = Repo(args.repo).commit(args.head).hexsha[:12]
        with contextlib.redirect_stdout(sys.stderr):  # cognee prints progress; stdout is the report
            result["cognee"] = asyncio.run(_remember(args, refs, f"gitgraph-{sha}"))
    return result


async def _remember(args: argparse.Namespace, refs: list[str], dataset: str) -> dict[str, Any]:
    import cognee
    from cognee.infrastructure.databases.graph import get_graph_engine

    from .links import link_rows
    from .source import gitgraph_source

    source = gitgraph_source(args.repo, refs=refs, ci_logs=args.ci or None)
    done = await cognee.remember(source, dataset_name=dataset, self_improvement=False)
    if done.status != "completed":
        raise ValueError(f"cognee could not remember the repo: {done.error}")
    links = await link_rows(source)
    nodes, edges = await (await get_graph_engine()).get_graph_data()
    tables = {str(node_id): props["name"] for node_id, props in nodes}
    rows = Counter(tables[str(target)] for _, target, name, _ in edges if name == "is_row_of")
    return {"dataset": dataset, "rows": dict(sorted(rows.items())), "links": links}


def _report(result: dict[str, Any]) -> str:
    lines = [
        f"Blast radius of {result['base']}...{result['head']} (up to {_hops(result['max_hops'])})",
        f"Changed files ({len(result['changed_files'])}): " + ", ".join(result["changed_files"]),
        f"Changed symbols ({len(result['changed_symbols'])}): "
        + ", ".join(result["changed_symbols"]),
        "",
        "At-risk services:",
    ]
    lines += [
        f"  {s['name']}  {_hops(s['hops'])}  {' -> '.join(s['path'])}" for s in result["services"]
    ]
    lines += ["  none"] * (not result["services"])
    lines += [f"At-risk symbols: {len(result['symbols'])} in {len(result['files'])} files"]
    if result["co_changed"]:
        lines += ["", "Co-changed files:"]
        lines += [f"  {c['path']}  ({len(c['shas'])} shared commits)" for c in result["co_changed"]]
    if result["ci_failures"]:
        lines += ["", "CI failures in at-risk services:"]
        lines += [
            f"  {f['job']}  {f['service']}  {f['test_id']}  {f['message']}"
            for f in result["ci_failures"]
        ]
    if "cognee" in result:
        graph = result["cognee"]
        lines += ["", f"Cognee graph (dataset {graph['dataset']}):"]
        lines += [f"  {table}: {count}" for table, count in graph["rows"].items()]
        lines += ["Links between rows:"]
        lines += [f"  {name}: {count}" for name, count in sorted(graph["links"].items())]
    return "\n".join(lines)


def _emit(result: dict[str, Any], as_json: bool) -> None:
    print(json.dumps(result, indent=2, sort_keys=True) if as_json else _report(result))


def _demo(args: argparse.Namespace) -> dict[str, Any]:
    import cognee

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cognee.config.system_root_directory(str(root / "system"))
        cognee.config.data_root_directory(str(root / "data"))
        ci = root / "ci"
        ci.mkdir()
        for name, xml in _DEMO_CI.items():
            (ci / name).write_text(xml)
        sample = build_sample_repo(root / "repo")
        return _blast(
            argparse.Namespace(
                repo=sample.path,
                base=sample.base,
                head=sample.head,
                ci=[ci],
                hops=4,
                remember=True,
            )
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cognee-gitgraph", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    blast = commands.add_parser("blast", help="what a branch can break")
    blast.add_argument("--repo", type=Path, required=True, help="git repository")
    blast.add_argument("--base", required=True, help="base ref, e.g. main")
    blast.add_argument("--head", required=True, help="head ref, e.g. the feature branch")
    blast.add_argument("--ci", type=Path, action="append", help="JUnit XML file or directory")
    blast.add_argument("--hops", type=int, default=4, help="caller hops to follow (default 4)")
    blast.add_argument("--remember", action="store_true", help="also load the repo into Cognee")
    demo = commands.add_parser("demo", help="blast the built-in sample repo through Cognee")
    for command in (blast, demo):
        command.add_argument("--json", action="store_true", help="print JSON only")
    blast.set_defaults(run=_blast)
    demo.set_defaults(run=_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.run(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _emit(result, args.json)
    return 0
