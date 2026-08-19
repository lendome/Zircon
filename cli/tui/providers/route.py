"""
RouteProvider — manages current view/route state.

Equivalent of OpenCode's RouteProvider. Tracks which view is active
(chat, task, help, status) and provides navigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class RouteState:
    current: str = "chat"
    history: list[str] = field(default_factory=list)

    def navigate(self, route: str) -> None:
        if route != self.current:
            self.history.append(self.current)
            self.current = route

    def back(self) -> None:
        if self.history:
            self.current = self.history.pop()


class RouteProvider(Provider):
    name = "route"

    def provide(self, registry: ContextRegistry) -> Any:
        state = RouteState()
        ctx = Context(name=self.name)
        ctx.set(state)
        registry.register(ctx)
        return state
