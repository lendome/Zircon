"""
Async file search — server-side ranked file search with client fallback.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class FileSearchResult:
    path: str = ""
    display: str = ""
    is_directory: bool = False
    score: float = 0.0


class AsyncFileSearch:
    """Async file search with frecency boosting. Keeps prev results while loading."""

    def __init__(self, frecency: Any = None) -> None:
        self._frecency = frecency
        self._loading = False
        self._prev: list[FileSearchResult] = []
        self._task: asyncio.Task | None = None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def results(self) -> list[FileSearchResult]:
        return self._prev

    async def search(self, query: str, directory: str, limit: int = 20) -> list[FileSearchResult]:
        """Search files matching query in directory. Returns ranked results."""
        self._loading = True
        try:
            results = await self._do_search(query, directory, limit)
            self._prev = results
            return results
        finally:
            self._loading = False

    async def _do_search(self, query: str, directory: str, limit: int) -> list[FileSearchResult]:
        """Client-side file search (fallback when no backend search available)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_search, query, directory, limit)

    def _sync_search(self, query: str, directory: str, limit: int) -> list[FileSearchResult]:
        """Synchronous file search using os.walk."""
        results: list[FileSearchResult] = []
        q = query.lower().strip()
        skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".zircon-code", "dist", "build"}

        try:
            for root, dirs, files in os.walk(directory):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for name in sorted(files + dirs):
                    if q and q not in name.lower():
                        continue
                    full = os.path.join(root, name)
                    try:
                        rel = os.path.relpath(full, directory).replace(os.sep, "/")
                        is_dir = os.path.isdir(full)
                    except (OSError, ValueError):
                        # Skip entries on other mounts/devices (e.g. \\.\nul)
                        # or otherwise unresolvable paths.
                        continue

                    freq = 0.0
                    if self._frecency is not None:
                        entry = self._frecency._entries.get(rel)
                        freq = entry.score if entry else 0.0
                    results.append(FileSearchResult(
                        path=rel, display=name, is_directory=is_dir, score=freq,
                    ))
                    if len(results) >= limit:
                        return results
        except (OSError, PermissionError):
            pass
        return results
