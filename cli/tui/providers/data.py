"""
DataProvider — session data (history, working set, modified files).

Equivalent of OpenCode's DataProvider. Caches agent state fetched from
the backend so components don't each query independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class DataState:
    status: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    async def refresh(self, transport: Any) -> None:
        try:
            self.status = await transport.get_status()
        except Exception:
            pass


class DataProvider(Provider):
    name = "data"

    def provide(self, registry: ContextRegistry) -> Any:
        state = DataState()
        ctx = Context(name=self.name)
        ctx.set(state)
        registry.register(ctx)
        return state
