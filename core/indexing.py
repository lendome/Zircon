"""Async indexing orchestrator with priority levels.

Prevents codebase indexing from blocking agent startup.
Returns best-available cached data immediately, schedules
higher-quality indexing as background tasks.

Priority levels:
  CRITICAL (P0) — synchronous, must complete (cache load)
  HIGH     (P1) — starts on first await, non-blocking (shallow scan, heuristic classify)
  NORMAL   (P2) — background within seconds (full repo map, AST parse, KG ingest)
  LOW      (P3) — fire-and-forget (embedding model, semantic index, AGENTS.md)
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable

from .constants import ZIRCON_DIR, zircon_path
from .task_manager import BackgroundTask, create_background_task

logger = logging.getLogger("agent.core.indexing")


class IndexPriority(IntEnum):
    CRITICAL = 0  # Synchronous, must complete before first LLM turn
    HIGH = 1      # Starts on first await, non-blocking
    NORMAL = 2    # Background catch-up
    LOW = 3       # Fire-and-forget after first response


@dataclass
class IndexSnapshot:
    """What's currently available from indexing."""

    repo_map_paths: dict[str, Any] = field(default_factory=dict)
    repo_map_text: str = ""
    repo_map_built: bool = False
    repo_map_progress: str = ""  # e.g. "147/983 files indexed"
    project_category: str = ""
    project_classified: bool = False
    kg_ready: bool = False
    embedder_ready: bool = False

    # Timing metadata
    build_started_at: float = 0.0
    build_completed_at: float = 0.0

    @property
    def is_stale(self) -> bool:
        """True if a newer build is in progress but not yet complete."""
        return self.build_started_at > self.build_completed_at

    @property
    def progress_pct(self) -> float:
        if not self.repo_map_progress:
            return 0.0
        try:
            done, total = self.repo_map_progress.split("/")
            return int(done) / max(1, int(total)) * 100
        except (ValueError, IndexError):
            return 0.0


class IndexingOrchestrator:
    """Manages async repo indexing with priority scheduling.

    Usage:
        orchestrator = IndexingOrchestrator(repo_path)
        snapshot = await orchestrator.get_state()  # returns best-available immediately
        orchestrator.submit_normal(coro)            # schedule P2 task
        orchestrator.submit_low(coro)               # schedule P3 task
    """

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self._snapshot = IndexSnapshot()
        self._background_tasks: dict[str, BackgroundTask] = {}
        self._classify_lock = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> IndexSnapshot:
        """Return current snapshot (always best-available, never blocks)."""
        return self._snapshot

    async def wait_for(self, min_priority: IndexPriority, timeout: float = 30.0) -> IndexSnapshot:
        """Wait up to *timeout* seconds for indexing at or above *min_priority* to complete.

        Returns the snapshot when satisfied, or current snapshot on timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = self._snapshot
            if min_priority <= IndexPriority.CRITICAL and snap.repo_map_built:
                return snap
            if min_priority <= IndexPriority.HIGH and snap.repo_map_built:
                return snap
            if min_priority <= IndexPriority.NORMAL and snap.build_completed_at > 0:
                return snap
            if min_priority <= IndexPriority.LOW:
                return snap  # low is fire-and-forget, always available
            await self._sleep(0.1)
        logger.debug("wait_for(P=%s) timed out after %.1fs", min_priority.name, timeout)
        return self._snapshot

    def submit_critical(self, coro_fn: Callable[[], Any], name: str = "") -> BackgroundTask:
        """Schedule a P0 task. Waits for completion (caller awaits)."""
        task = self._schedule(coro_fn, name=name or "critical", priority="critical")
        return task

    def submit_high(self, coro_fn: Callable[[], Any], name: str = "") -> BackgroundTask:
        """Schedule a P1 task (background, non-blocking)."""
        task = self._schedule(coro_fn, name=name or "high", priority="high")
        self._background_tasks[task.id] = task
        return task

    def submit_normal(self, coro_fn: Callable[[], Any], name: str = "") -> BackgroundTask:
        """Schedule a P2 task (background, ~seconds delay)."""
        task = self._schedule(coro_fn, name=name or "normal", priority="normal")
        self._background_tasks[task.id] = task
        return task

    def submit_low(self, coro_fn: Callable[[], Any], name: str = "") -> BackgroundTask:
        """Schedule a P3 task (fire-and-forget)."""
        task = self._schedule(coro_fn, name=name or "low", priority="low")
        self._background_tasks[task.id] = task
        return task

    def update_snapshot(self, **kwargs: Any) -> None:
        """Atomically update snapshot fields."""
        for k, v in kwargs.items():
            if hasattr(self._snapshot, k):
                setattr(self._snapshot, k, v)
        logger.debug("snapshot updated: %s", kwargs)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _schedule(self, coro_fn: Callable[[], Any], name: str, priority: str) -> BackgroundTask:
        return create_background_task(
            repo_path=self.repo_path,
            coro=coro_fn(),
            name=f"idx:{priority}:{name}",
            metadata={"priority": priority, "type": "indexing"},
        )

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio
        await asyncio.sleep(seconds)