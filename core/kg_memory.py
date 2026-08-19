from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import networkx as nx

from .constants import ZIRCON_DIR, ensure_zircon_dir, zircon_path

NODE_TYPES = ("file", "function", "class", "method", "symbol", "error", "task", "concept")
EDGE_TYPES = ("contains", "calls", "imports", "edits", "fixes", "relates_to", "depends_on", "defined_in")


class KnowledgeGraphMemory:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        ensure_zircon_dir(self.repo_path)
        self.db_path = zircon_path(self.repo_path, "knowledge_graph.db")
        self._init_db()
        self._graph: nx.DiGraph | None = None

    def _init_db(self):
        with self._db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    weight REAL NOT NULL DEFAULT 1.0,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    type TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    FOREIGN KEY (source) REFERENCES nodes(id),
                    FOREIGN KEY (target) REFERENCES nodes(id),
                    UNIQUE(source, target, type)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One transaction on a connection that is closed afterwards.

        sqlite3's own context manager only commits/rolls back — it never
        closes. Leaking a connection per write exhausts the process file
        descriptor limit while indexing a large repo (errno 24).
        """
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _put_node(
        conn: sqlite3.Connection,
        node_id: str,
        node_type: str,
        data: dict | None,
        weight: float,
        now: str,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO nodes (id, type, data, weight, updated_at) VALUES (?, ?, ?, ?, ?)",
            (node_id, node_type, json.dumps(data or {}, default=str), weight, now),
        )

    @staticmethod
    def _put_edge(
        conn: sqlite3.Connection,
        source: str,
        target: str,
        edge_type: str,
        weight: float = 1.0,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO edges (source, target, type, weight) VALUES (?, ?, ?, ?)",
            (source, target, edge_type, weight),
        )

    def _load_graph(self) -> nx.DiGraph:
        if self._graph is not None:
            return self._graph
        G = nx.DiGraph()
        with self._db() as conn:
            for row in conn.execute("SELECT id, type, data, weight FROM nodes"):
                G.add_node(row[0], type=row[1], data=json.loads(row[2]), weight=row[3])
            for row in conn.execute("SELECT source, target, type, weight FROM edges"):
                if G.has_node(row[0]) and G.has_node(row[1]):
                    G.add_edge(row[0], row[1], type=row[2], weight=row[3])
        self._graph = G
        return G

    def _invalidate(self):
        self._graph = None

    def add_node(self, node_id: str, node_type: str, data: dict | None = None, weight: float = 1.0):
        if node_type not in NODE_TYPES:
            return
        now = datetime.utcnow().isoformat()
        with self._db() as conn:
            self._put_node(conn, node_id, node_type, data, weight, now)
        self._invalidate()

    def add_edge(self, source: str, target: str, edge_type: str, weight: float = 1.0):
        if edge_type not in EDGE_TYPES:
            return
        with self._db() as conn:
            self._put_edge(conn, source, target, edge_type, weight)
        self._invalidate()

    def ingest_file_structure(self, rel_path: str, symbols: list[dict]):
        # One connection and one transaction for the whole file — this runs
        # per file during repo indexing, so per-symbol connections are both
        # slow and an fd-exhaustion hazard.
        file_id = f"file:{rel_path}"
        now = datetime.utcnow().isoformat()
        with self._db() as conn:
            self._put_node(conn, file_id, "file", {"path": rel_path}, 1.0, now)
            for sym in symbols:
                name = sym["name"]
                kind = sym.get("kind", "symbol")
                line = sym.get("line", 0)
                parent = sym.get("parent")

                sym_id = f"{kind}:{rel_path}:{name}"
                self._put_node(conn, sym_id, kind if kind in NODE_TYPES else "symbol", {
                    "name": name, "file": rel_path, "line": line, "parent": parent,
                }, 1.0, now)
                self._put_edge(conn, file_id, sym_id, "contains")

                if parent:
                    parent_id = f"{'class'}:{rel_path}:{parent}"
                    self._put_edge(conn, parent_id, sym_id, "contains")
        self._invalidate()

    def ingest_edit(self, task_desc: str, file_path: str, symbols_touched: list[str]):
        task_id = f"task:{hash(task_desc) % 1000000}"
        self.add_node(task_id, "task", {"description": task_desc[:200]})
        file_id = f"file:{file_path}"
        self.add_node(file_id, "file", {"path": file_path})
        self.add_edge(task_id, file_id, "edits")
        for sym_name in symbols_touched:
            sym_id = f"symbol:{file_path}:{sym_name}"
            self.add_node(sym_id, "symbol", {"name": sym_name, "file": file_path})
            self.add_edge(task_id, sym_id, "edits")
            self.add_edge(sym_id, file_id, "defined_in")

    def ingest_import(self, file_path: str, import_path: str):
        src = f"file:{file_path}"
        tgt = f"file:{import_path}"
        self.add_node(src, "file", {"path": file_path})
        self.add_node(tgt, "file", {"path": import_path})
        self.add_edge(src, tgt, "imports")

    def ingest_error(self, error_text: str, file_path: str, fix_desc: str = ""):
        err_id = f"error:{hash(error_text) % 1000000}"
        self.add_node(err_id, "error", {"message": error_text[:300], "file": file_path})
        file_id = f"file:{file_path}"
        self.add_node(file_id, "file", {"path": file_path})
        self.add_edge(err_id, file_id, "relates_to")
        if fix_desc:
            task_id = f"task:{hash(fix_desc) % 1000000}"
            self.add_node(task_id, "task", {"description": fix_desc[:200]})
            self.add_edge(task_id, err_id, "fixes")

    def query_related(self, name: str, node_type: str = "", depth: int = 2, max_nodes: int = 20) -> list[dict]:
        G = self._load_graph()
        matches = []
        name_lower = name.lower()
        for nid, ndata in G.nodes(data=True):
            if name_lower in nid.lower():
                if node_type and ndata.get("type") != node_type:
                    continue
                matches.append(nid)
        if not matches:
            return []

        visited = set()
        results = []
        for start in matches[:3]:
            for node in nx.bfs_tree(G, start, depth_limit=depth).nodes():
                if node in visited or len(results) >= max_nodes:
                    break
                visited.add(node)
                ndata = G.nodes[node]
                results.append({
                    "id": node,
                    "type": ndata.get("type", ""),
                    "data": ndata.get("data", {}),
                })
        return results

    def get_context_for_task(self, task: str, max_nodes: int = 15) -> str:
        words = [w for w in task.lower().split() if len(w) > 3]
        if not words:
            return ""
        G = self._load_graph()
        scores: dict[str, float] = defaultdict(float)
        for nid, ndata in G.nodes(data=True):
            nid_lower = nid.lower()
            d = ndata.get("data", {})
            searchable = f"{nid_lower} {d.get('name', '')} {d.get('path', '')} {d.get('description', '')}".lower()
            for w in words:
                if w in searchable:
                    scores[nid] += 1.0
            for _, _, edata in G.edges(nid, data=True):
                if edata.get("type") == "imports":
                    scores[nid] += 0.3

        if not scores:
            return ""

        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        visited = set()
        lines = []
        for start, _ in top:
            for node in nx.bfs_tree(G, start, depth_limit=2).nodes():
                if node in visited or len(visited) >= max_nodes:
                    continue
                visited.add(node)
                ndata = G.nodes[node]
                data = ndata.get("data", {})
                ntype = ndata.get("type", "")
                if ntype == "file":
                    lines.append(f"  file: {data.get('path', node)}")
                elif ntype in ("function", "class", "method"):
                    lines.append(f"  {ntype}: {data.get('name', '?')} @ {data.get('file', '?')}:{data.get('line', '?')}")
                elif ntype == "error":
                    lines.append(f"  error: {data.get('message', '')[:80]}")
        return "\n".join(lines) if lines else ""

    def get_file_imports(self, file_path: str) -> list[str]:
        G = self._load_graph()
        file_id = f"file:{file_path}"
        if not G.has_node(file_id):
            return []
        return [
            G.nodes[t].get("data", {}).get("path", t)
            for _, t in G.out_edges(file_id)
            if G.edges[_, t].get("type") == "imports" and G.has_node(t)
        ]

    def batch_ingest(self, nodes: list[tuple[str, str, dict | None, float]],
                     edges: list[tuple[str, str, str, float]] | None = None):
        """Batch-insert multiple nodes and edges in a single transaction.
        
        Args:
            nodes: list of (node_id, node_type, data, weight) tuples
            edges: optional list of (source, target, edge_type, weight) tuples
        """
        if not nodes and not edges:
            return
        now = datetime.utcnow().isoformat()
        with self._db() as conn:
            conn.execute("BEGIN")
            try:
                for node_id, node_type, data, weight in nodes:
                    if node_type not in NODE_TYPES:
                        continue
                    d = json.dumps(data or {}, default=str)
                    conn.execute(
                        "INSERT OR REPLACE INTO nodes (id, type, data, weight, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (node_id, node_type, d, weight, now),
                    )
                if edges:
                    for source, target, edge_type, weight in edges:
                        if edge_type not in EDGE_TYPES:
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO edges (source, target, type, weight) "
                            "VALUES (?, ?, ?, ?)",
                            (source, target, edge_type, weight),
                        )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self._invalidate()

    def clear(self):
        with self._db() as conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
        self._invalidate()
