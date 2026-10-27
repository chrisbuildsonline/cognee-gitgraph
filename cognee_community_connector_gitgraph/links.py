"""Row-to-row edges in Cognee's graph, written after ``cognee.remember``.

Cognee draws row-to-row edges only from foreign keys it reads off the staging database, and dlt's
sqlalchemy destination never creates any, so ingested rows come out linked to their table node
and to nothing else. ``link_rows`` adds the missing edges between the row nodes cognee already
wrote. The edges are named the way cognee names its own foreign-key edges,
``{column}_references_{table}``:

- ``sha_references_commits``: file_changes -> commits (``file_changes.sha == commits.id``)
- ``from_symbol_references_symbols``: references -> the symbol that makes the reference
  (none for an import at module level, where ``from_symbol`` is null)
- ``to_symbol_references_symbols``: references -> the symbol being referenced
- ``commit_sha_references_commits``: ci_failures -> commits, when ``commit_sha`` is a commit id
  or the unique prefix of one (CI systems often report a short sha)
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from cognee.infrastructure.databases.graph import get_graph_engine

Row = Mapping[str, Any]
RowKey = tuple[str, str]  # (table, id)
Edge = tuple[RowKey, RowKey, str]  # (source row, target row, relationship)

_ROW_ID = re.compile(r"^  id: (.*)$", re.MULTILINE)


def build_edges(tables: Mapping[str, Iterable[Row]]) -> list[Edge]:
    """The sorted, deduplicated edges between the rows of ``tables`` (table name -> rows).

    An edge is only made when both ends exist, so a dangling reference or an unknown commit
    gives nothing. Tables that are absent (``ci_failures`` without CI logs) give no edges.
    """
    rows = {name: list(table) for name, table in tables.items()}
    commits = {r["id"] for r in rows.get("commits", [])}
    symbols = {r["id"] for r in rows.get("symbols", [])}
    edges: set[Edge] = set()

    def commit_for(sha: str | None) -> str | None:
        if not sha:
            return None
        if sha in commits:
            return sha
        matches = [c for c in commits if c.startswith(sha)]
        return matches[0] if len(matches) == 1 else None

    for row in rows.get("file_changes", []):
        if row["sha"] in commits:
            key, target = ("file_changes", row["id"]), ("commits", row["sha"])
            edges.add((key, target, "sha_references_commits"))
    for row in rows.get("references", []):
        key = ("references", row["id"])
        if row.get("from_symbol") in symbols:
            edges.add((key, ("symbols", row["from_symbol"]), "from_symbol_references_symbols"))
        if row.get("to_symbol") in symbols:
            edges.add((key, ("symbols", row["to_symbol"]), "to_symbol_references_symbols"))
    for row in rows.get("ci_failures", []):
        commit = commit_for(row.get("commit_sha"))
        if commit is not None:
            key = ("ci_failures", row["id"])
            edges.add((key, ("commits", commit), "commit_sha_references_commits"))
    return sorted(edges)


async def _row_node_ids() -> dict[RowKey, str]:
    """Cognee's node id for each row, found through the row's ``is_row_of`` edge to its table.

    A row node is a document chunk whose text is cognee's rendering of the row, starting
    ``Table: <name>`` and listing ``  id: <value>`` under ``Row Data:``.
    """
    nodes, edges = await (await get_graph_engine()).get_graph_data()
    props = {str(node_id): p for node_id, p in nodes}
    found: dict[RowKey, str] = {}
    for source, target, relationship, _ in edges:
        if relationship != "is_row_of" or str(source) not in props or str(target) not in props:
            continue
        match = _ROW_ID.search(props[str(source)].get("text") or "")
        if match:
            found.setdefault((props[str(target)]["name"], match.group(1)), str(source))
    return found


async def link_rows(source: Any) -> dict[str, int]:
    """Write the edges of ``build_edges`` between the row nodes of an already remembered ``source``.

    Safe to run again: cognee merges an edge on (from, to, relationship), so a second run changes
    nothing. Returns the edges written per relationship plus ``"missing"``, the edges skipped
    because a row has no node in the graph (never an error).
    """
    tables = {name: list(resource) for name, resource in source.resources.items()}
    node_ids = await _row_node_ids()
    summary: dict[str, int] = {"missing": 0}
    batch = []
    for from_row, to_row, relationship in build_edges(tables):
        if from_row not in node_ids or to_row not in node_ids:
            summary["missing"] += 1
            continue
        from_id, to_id = node_ids[from_row], node_ids[to_row]
        batch.append(
            (
                from_id,
                to_id,
                relationship,
                {
                    "source_node_id": from_id,
                    "target_node_id": to_id,
                    "relationship_name": relationship,
                    "edge_text": relationship.replace("_", " "),
                    "source_table": from_row[0],
                    "target_table": to_row[0],
                },
            )
        )
        summary[relationship] = summary.get(relationship, 0) + 1
    if batch:
        await (await get_graph_engine()).add_edges(batch)
    return summary
