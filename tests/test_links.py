"""The pure edge-spec builder: which rows link to which, from the source's own rows."""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from cognee_community_connector_gitgraph import build_sample_repo, gitgraph_source
from cognee_community_connector_gitgraph.links import build_edges

FIXTURES = Path(__file__).parent / "fixtures" / "ci"

IN_COMMIT = "sha_references_commits"
AT_COMMIT = "commit_sha_references_commits"
FROM_SYMBOL = "from_symbol_references_symbols"
TO_SYMBOL = "to_symbol_references_symbols"

Tables = dict[str, list[dict[str, Any]]]


@pytest.fixture
def tables(tmp_path: Path) -> Tables:
    source = gitgraph_source(build_sample_repo(tmp_path / "repo").path, ci_logs=[FIXTURES])
    return {name: list(resource) for name, resource in source.resources.items()}


def _counts(tables: Tables) -> Counter[str]:
    return Counter(relationship for _, _, relationship in build_edges(tables))


def test_sample_edge_counts(tables: Tables) -> None:
    # 15 file changes; 10 references, 5 of them calls made inside a symbol (the imports have no
    # owner); the 2 CI failures name no commit that exists (one has no sha, one an unknown sha).
    assert _counts(tables) == {IN_COMMIT: 15, FROM_SYMBOL: 5, TO_SYMBOL: 10}


def test_edges_are_sorted_and_deterministic(tables: Tables) -> None:
    edges = build_edges(tables)
    assert edges == sorted(set(edges))
    shuffled = {name: list(reversed(rows)) for name, rows in tables.items()}
    assert build_edges(shuffled) == edges


def test_every_edge_ends_at_an_existing_row(tables: Tables) -> None:
    keys = {(name, row["id"]) for name, rows in tables.items() for row in rows}
    for source, target, _ in build_edges(tables):
        assert source in keys
        assert target in keys


def test_null_from_symbol_gives_no_from_edge(tables: Tables) -> None:
    imports = {r["id"] for r in tables["references"] if r["from_symbol"] is None}
    assert len(imports) == 5
    from_sources = {s[1] for s, _, rel in build_edges(tables) if rel == FROM_SYMBOL}
    assert not imports & from_sources
    to_sources = {s[1] for s, _, rel in build_edges(tables) if rel == TO_SYMBOL}
    assert imports <= to_sources


def test_edges_use_row_keys_of_table_and_id(tables: Tables) -> None:
    edges = build_edges(tables)
    sha = tables["file_changes"][0]["sha"]
    row = tables["file_changes"][0]["id"]
    assert (("file_changes", row), ("commits", sha), IN_COMMIT) in edges
    call = next(r for r in tables["references"] if r["from_symbol"] == "billing/invoice.py::charge")
    assert (("references", call["id"]), ("symbols", "billing/invoice.py::charge"), FROM_SYMBOL) in (
        edges
    )


def test_dangling_targets_make_no_edges(tables: Tables) -> None:
    tables["file_changes"].append({"id": "dead:x.py", "sha": "dead" * 10})
    tables["references"].append(
        {"id": "r1", "from_symbol": "nope::a", "to_symbol": "nope::b", "kind": "call"}
    )
    assert _counts(tables) == {IN_COMMIT: 15, FROM_SYMBOL: 5, TO_SYMBOL: 10}


def test_duplicate_rows_give_one_edge(tables: Tables) -> None:
    tables["file_changes"] += tables["file_changes"]
    assert _counts(tables) == {IN_COMMIT: 15, FROM_SYMBOL: 5, TO_SYMBOL: 10}


def _ci(commit_sha: str | None, row_id: str = "job:t") -> dict[str, Any]:
    return {"id": row_id, "commit_sha": commit_sha}


def test_ci_failure_links_by_full_sha_and_unique_prefix(tables: Tables) -> None:
    sha = tables["commits"][0]["id"]
    tables["ci_failures"] = [_ci(sha, "a"), _ci(sha[:7], "b"), _ci(None, "c"), _ci("0" * 7, "d")]
    at = [(s[1], t[1]) for s, t, rel in build_edges(tables) if rel == AT_COMMIT]
    assert at == [("a", sha), ("b", sha)]


def test_ambiguous_prefix_makes_no_edge() -> None:
    commits = [{"id": "abcdef1" + "0" * 33}, {"id": "abcdef1" + "1" * 33}]
    tables: Tables = {"commits": commits, "ci_failures": [_ci("abcdef1"), _ci("abcdef10")]}
    at = [(s[1], t[1]) for s, t, rel in build_edges(tables) if rel == AT_COMMIT]
    assert at == [("job:t", commits[0]["id"])]


def test_missing_tables_are_fine() -> None:
    assert build_edges({}) == []
    assert build_edges({"commits": [{"id": "a" * 40}]}) == []
