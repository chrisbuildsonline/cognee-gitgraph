"""The source ingested by real cognee (local embedded stores, no LLM key)."""

from collections import Counter
from pathlib import Path
from typing import Any

import cognee
import pytest
from cognee.infrastructure.databases.graph import get_graph_engine

from cognee_community_connector_gitgraph import build_sample_repo, gitgraph_source

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "ci"
COUNTS = {"commits": 8, "file_changes": 15, "symbols": 8, "references": 10, "ci_failures": 2}


@pytest.fixture(autouse=True)
def keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_API_KEY", "OPENAI_API_KEY", "EMBEDDING_API_KEY"):
        monkeypatch.delenv(var, raising=False)


async def _remember(repo: Path) -> None:
    result = await cognee.remember(
        gitgraph_source(repo, ci_logs=[FIXTURES]), dataset_name="gitgraph", self_improvement=False
    )
    assert result.status == "completed", result.error


async def _row_nodes() -> dict[str, list[dict[str, Any]]]:
    """Row nodes by table: cognee links each row to its SchemaTable node with ``is_row_of``."""
    nodes, edges = await (await get_graph_engine()).get_graph_data()
    by_id = {str(node_id): props for node_id, props in nodes}
    rows: dict[str, list[dict[str, Any]]] = {}
    for source, target, relationship, _ in edges:
        if relationship == "is_row_of":
            rows.setdefault(by_id[str(target)]["name"], []).append(by_id[str(source)])
    return rows


async def test_remember_puts_every_row_in_the_graph(tmp_path: Path) -> None:
    await _remember(build_sample_repo(tmp_path / "repo").path)

    rows = await _row_nodes()

    assert {table: len(r) for table, r in rows.items()} == COUNTS
    assert any("shared/auth.py::verify_token" in r["text"] for r in rows["symbols"])


async def test_remembering_twice_adds_no_row_nodes(tmp_path: Path) -> None:
    repo = build_sample_repo(tmp_path / "repo").path
    await _remember(repo)
    await _remember(repo)

    rows = await _row_nodes()

    assert Counter({table: len(r) for table, r in rows.items()}) == Counter(COUNTS)
