"""
Local provider — ephemeral UI state that doesn't survive restarts.

Holds: current agent, current model, recent models, favorites,
session quick-switch, permission mode, model variant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..context import Context, ContextRegistry
from ..reactive.signal import Signal, signal, computed
from .base import Provider


@dataclass
class AgentState:
    current_name: str = "default"
    agents: list[dict[str, Any]] = field(default_factory=lambda: [{"name": "default", "color": "cyan"}])

    def current(self) -> dict[str, Any]:
        for a in self.agents:
            if a["name"] == self.current_name:
                return a
        return {"name": self.current_name, "color": "cyan"}

    def set(self, name: str) -> None:
        self.current_name = name

    def move(self, direction: int) -> None:
        names = [a["name"] for a in self.agents]
        if self.current_name in names:
            idx = names.index(self.current_name)
            idx = (idx + direction) % len(names)
            self.current_name = names[idx]

    def color(self, name: str | None = None) -> str:
        n = name or self.current_name
        for a in self.agents:
            if a["name"] == n:
                return a.get("color", "cyan")
        return "cyan"


@dataclass
class ModelState:
    current_provider: str = ""
    current_model: str = ""
    recent: list[dict[str, str]] = field(default_factory=list)
    favorites: list[dict[str, str]] = field(default_factory=list)
    variant: str = ""
    ready: bool = False

    def current(self) -> dict[str, str]:
        return {"providerID": self.current_provider, "modelID": self.current_model}

    def set(self, model: dict[str, str], recent: bool = True) -> None:
        self.current_provider = model.get("providerID", "")
        self.current_model = model.get("modelID", "")
        if recent and model not in self.recent:
            self.recent.insert(0, model)
            self.recent = self.recent[:10]

    def parsed(self) -> dict[str, str]:
        return {"provider": self.current_provider, "model": self.current_model}

    def cycle(self, direction: int) -> None:
        if not self.recent:
            return
        for i, m in enumerate(self.recent):
            if m.get("modelID") == self.current_model and m.get("providerID") == self.current_provider:
                idx = (i + direction) % len(self.recent)
                self.set(self.recent[idx])
                return
        self.set(self.recent[0])

    def cycle_favorite(self, direction: int) -> None:
        if not self.favorites:
            return
        for i, m in enumerate(self.favorites):
            if m.get("modelID") == self.current_model:
                idx = (i + direction) % len(self.favorites)
                self.set(self.favorites[idx])
                return
        self.set(self.favorites[0])


@dataclass
class LocalState:
    """Ephemeral UI state — doesn't survive restarts."""

    agent: AgentState = field(default_factory=AgentState)
    model: ModelState = field(default_factory=ModelState)
    permission_mode: str = "normal"  # auto | normal

    def toggle_permission(self) -> None:
        self.permission_mode = "auto" if self.permission_mode == "normal" else "normal"


class LocalProvider(Provider):
    name = "local"

    def provide(self, registry: ContextRegistry) -> Any:
        local = LocalState()
        ctx = Context(name=self.name)
        ctx.set(local)
        registry.register(ctx)
        return local
