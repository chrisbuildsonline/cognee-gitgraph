"""The blast radius of a change: what calls the changed code, from the symbol and reference rows.

``blast_radius`` is pure: it takes the same table-name -> rows mapping as ``links.build_edges``
and reads nothing else. It walks the reference graph backwards (callee -> caller) from the changed
symbols, so the answer is every symbol, file and top-level directory ("service") that can reach
the change, how many hops away, and one shortest chain of ids that proves it.
"""

from collections.abc import Iterable, Mapping
from typing import Any

import networkx as nx

Row = Mapping[str, Any]
Node = tuple[int, str]  # (0, symbol id) or (1, file path); files are leaves nothing references

_SYMBOL, _FILE = 0, 1


def _service(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "."


def _ci_service(failure: Row) -> str:
    if failure.get("file"):
        return _service(failure["file"])
    return str(failure.get("test_id") or "").split("::", 1)[0].split(".", 1)[0]


def _split(item: str) -> tuple[str, int | None]:
    path, _, line = item.rpartition(":")
    return (path, int(line)) if path and line.isdigit() else (item, None)


def _changed_symbols(symbols: list[Row], changed: list[str]) -> set[str]:
    by_file: dict[str, list[Row]] = {}
    for row in symbols:
        by_file.setdefault(row["file"], []).append(row)
    hit: set[str] = set()
    for item in changed:
        path, line = _split(item)
        rows = by_file.get(path, [])
        # ponytail: symbols carry only a start line, so a line after a function's end still maps to
        # it; add end_lineno to the symbol rows if that precision ever matters.
        above = [r for r in rows if line is not None and r["line"] <= line]
        if above:
            hit.add(max(above, key=lambda r: (r["line"], r["id"]))["id"])
        else:
            hit.update(r["id"] for r in rows)
    return hit


def blast_radius(
    tables: Mapping[str, Iterable[Row]], changed: Iterable[str], max_hops: int = 4
) -> dict[str, Any]:
    """What a change can break, as JSON-serialisable data with every list sorted.

    ``changed`` items are repo paths (every symbol in the file changes) or ``path:line`` (the
    symbol in that file starting at or before the line, else every symbol in the file). Callers
    are followed up to ``max_hops``; ``symbols``, ``files`` and ``services`` carry the hop count
    and ``path``, the ids from a changed symbol to the hit (shortest, ties by sorted ids).
    ``services`` leaves out the top-level directories of the changed files; ``co_changed`` lists
    files that shared a commit with a changed file and adds no services. ``ci_failures`` are those
    of an at-risk or changed service. Raises ``ValueError`` unless ``max_hops`` is an int >= 0.
    """
    if not isinstance(max_hops, int) or isinstance(max_hops, bool) or max_hops < 0:
        raise ValueError(f"max_hops must be an int >= 0, got {max_hops!r}")
    rows = {name: list(table) for name, table in tables.items()}
    items = list(changed)
    symbols = rows.get("symbols", [])
    file_of = {r["id"]: r["file"] for r in symbols}
    changed_files = sorted({_split(item)[0] for item in items})
    changed_ids = _changed_symbols(symbols, items)

    graph: nx.DiGraph = nx.DiGraph()
    graph.add_nodes_from(sorted((_SYMBOL, sid) for sid in file_of))
    edges: set[tuple[Node, Node]] = set()
    for ref in rows.get("references", []):
        owner, target = ref.get("from_symbol"), ref.get("to_symbol")
        if target not in file_of:
            continue
        if owner is None and ref.get("from_file"):
            edges.add(((_FILE, str(ref["from_file"])), (_SYMBOL, str(target))))
        elif owner in file_of:
            edges.add(((_SYMBOL, str(owner)), (_SYMBOL, str(target))))
    graph.add_edges_from(sorted(edges))

    hops: dict[Node, int] = {(_SYMBOL, sid): 0 for sid in changed_ids}
    parent: dict[Node, Node] = {}
    frontier = sorted(hops)
    for depth in range(1, max_hops + 1):
        reached: list[Node] = []
        for node in frontier:
            for caller in sorted(graph.predecessors(node)):
                if caller not in hops:
                    hops[caller] = depth
                    parent[caller] = node
                    reached.append(caller)
        frontier = sorted(reached)

    def chain(node: Node) -> list[str]:
        ids = [node[1]]
        while node in parent:
            node = parent[node]
            ids.append(node[1])
        return ids[::-1]

    hit_symbols = [
        {"id": n[1], "file": file_of[n[1]], "hops": h, "path": chain(n)}
        for n, h in sorted(hops.items())
        if h > 0 and n[0] == _SYMBOL
    ]
    # Per file, the closest hit wins; a symbol's chain beats an import's chain at equal hops.
    best_file: dict[str, tuple[int, int, list[str]]] = {}
    for node, h in hops.items():
        if h == 0:
            continue
        path = node[1] if node[0] == _FILE else file_of[node[1]]
        if path in changed_files:
            continue
        candidate = (h, node[0], chain(node))
        if path not in best_file or candidate < best_file[path]:
            best_file[path] = candidate
    files = [
        {"file": path, "hops": h, "path": ids} for path, (h, _, ids) in sorted(best_file.items())
    ]
    changed_services = {_service(path) for path in changed_files}
    best_service: dict[str, tuple[int, int, list[str]]] = {}
    for path, candidate in best_file.items():
        name = _service(path)
        if name not in changed_services and (
            name not in best_service or candidate < best_service[name]
        ):
            best_service[name] = candidate
    services = [
        {"name": name, "hops": h, "path": ids} for name, (h, _, ids) in sorted(best_service.items())
    ]

    shared: dict[str, set[str]] = {}
    shas_of_changed = {
        row["sha"] for row in rows.get("file_changes", []) if row["path"] in changed_files
    }
    for row in rows.get("file_changes", []):
        if row["sha"] in shas_of_changed and row["path"] not in changed_files:
            shared.setdefault(row["path"], set()).add(row["sha"])
    co_changed = [
        {"path": path, "shas": sorted(shas)}
        for path, shas in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    in_scope = set(best_service) | changed_services
    ci_failures = [
        {
            "id": f["id"],
            "job": f.get("job"),
            "test_id": f.get("test_id"),
            "service": _ci_service(f),
            "message": f.get("message"),
        }
        for f in sorted(rows.get("ci_failures", []), key=lambda f: f["id"])
        if _ci_service(f) in in_scope
    ]
    return {
        "changed_files": changed_files,
        "changed_symbols": sorted(changed_ids),
        "max_hops": max_hops,
        "symbols": hit_symbols,
        "files": files,
        "services": services,
        "co_changed": co_changed,
        "ci_failures": ci_failures,
    }
