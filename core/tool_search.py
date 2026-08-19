from __future__ import annotations

from typing import Any

from .embeddings import LocalEmbedder
from ..tools.base import Tool


class ToolSearchIndex:
    def __init__(self, cache_dir: str | None = None):
        self._tools: dict[str, Tool] = {}
        self._descriptions: dict[str, str] = {}
        self._categories: dict[str, list[str]] = {
            "file_read": ["read", "view", "show", "open", "display", "cat"],
            "file_edit": ["edit", "modify", "change", "replace", "update", "write", "fix", "patch"],
            "file_create": ["create", "new", "add file", "write file", "generate"],
            "file_delete": ["delete", "remove", "rm"],
            "search": ["search", "find", "grep", "locate", "look for", "where is"],
            "explore": ["explore", "browse", "structure", "list", "glob", "directory", "tree"],
            "symbol": ["symbol", "function", "class", "method", "definition", "declaration",
                       "body", "references", "callers", "who calls", "usages",
                       "call graph", "callee", "ast", "enclosing", "scope", "block", "range"],
            "shell": ["run", "execute", "command", "shell", "bash", "terminal", "test", "pytest", "lint"],
            "dev": ["deterministic", "determinism", "profile", "profiler", "profiling",
                    "benchmark", "flaky", "slow", "bottleneck", "golden", "capture output"],
            "web": ["fetch", "url", "http", "web", "download", "api"],
        }
        self._embedder: LocalEmbedder | None = None
        self._tool_embs: dict[str, Any] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool
        params = ", ".join(tool.schema.get("properties", {}).keys())
        self._descriptions[tool.name] = f"{tool.name}({params}): {tool.description}"

    def register_all(self, tools: list[Tool]):
        for t in tools:
            self.register(t)

    def get_relevant_tools(self, query: str, max_tools: int = 6) -> list[Tool]:
        query_lower = query.lower()
        matched = set()
        for cat_name, keywords in self._categories.items():
            for kw in keywords:
                if kw in query_lower:
                    for tool_name, desc in self._descriptions.items():
                        if kw in desc.lower() or kw in tool_name:
                            matched.add(tool_name)
                    break

        if matched:
            result = [self._tools[n] for n in matched if n in self._tools]
            if result:
                return result[:max_tools]

        return self._semantic_search(query, max_tools)

    def _semantic_search(self, query: str, k: int) -> list[Tool]:
        if not self._embedder:
            try:
                self._embedder = LocalEmbedder()
                names = list(self._descriptions.keys())
                descs = [self._descriptions[n] for n in names]
                embs = self._embedder.embed_documents(descs)
                for i, name in enumerate(names):
                    self._tool_embs[name] = embs[i]
            except Exception:
                return list(self._tools.values())[:k]

        import numpy as np
        q_emb = self._embedder.embed_query(query)
        scores = {}
        for name, emb in self._tool_embs.items():
            scores[name] = float(np.dot(q_emb, emb))
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        return [self._tools[n] for n, _ in top if n in self._tools]

    def get_schemas_for_query(self, query: str, max_tools: int = 6) -> list[dict]:
        tools = self.get_relevant_tools(query, max_tools)
        return [t.to_openai_schema() for t in tools]

    def get_all_schemas(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)
