"""
Frecency provider — frequency + recency scoring for file mentions.

Used by the @mention autocomplete to rank files by how often and how
recently they were mentioned. Files mentioned more often and more
recently appear first.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class FrecencyEntry:
    """A single frecency score entry."""

    path: str
    count: int = 0
    last_used: float = 0.0

    @property
    def score(self) -> float:
        """Frecency score = count * recency_decay."""
        if self.count == 0:
            return 0.0
        age = time.time() - self.last_used
        # Exponential decay: recent mentions score higher
        decay = 1.0 / (1.0 + age / 3600.0)  # half-life of 1 hour
        return self.count * decay


class FrecencyProvider(Provider):
    name = "frecency"

    def __init__(self) -> None:
        self._entries: dict[str, FrecencyEntry] = {}

    def track(self, path: str) -> None:
        """Record that a file was mentioned."""
        if path not in self._entries:
            self._entries[path] = FrecencyEntry(path=path)
        self._entries[path].count += 1
        self._entries[path].last_used = time.time()

    def rank(self, query: str, max_results: int = 10) -> list[str]:
        """Rank files by frecency score, filtered by query."""
        entries = list(self._entries.values())
        if query:
            q = query.lower()
            entries = [e for e in entries if q in e.path.lower()]
        entries.sort(key=lambda e: e.score, reverse=True)
        return [e.path for e in entries[:max_results]]

    def provide(self, registry: ContextRegistry) -> Any:
        ctx = Context(name=self.name)
        ctx.set(self)
        registry.register(ctx)
        return self
