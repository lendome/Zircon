"""
PermissionProvider — manages tool permission state.

Equivalent of OpenCode's PermissionProvider. Tracks which tools are
auto-approved vs require confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class PermissionState:
    auto_approve: set[str] = field(default_factory=lambda: {"read_file", "glob", "grep", "list_dir"})
    denied: set[str] = field(default_factory=set)

    def is_approved(self, tool: str) -> bool:
        return tool in self.auto_approve and tool not in self.denied

    def approve(self, tool: str) -> None:
        self.auto_approve.add(tool)
        self.denied.discard(tool)

    def deny(self, tool: str) -> None:
        self.denied.add(tool)


class PermissionProvider(Provider):
    name = "permission"

    def provide(self, registry: ContextRegistry) -> Any:
        state = PermissionState()
        ctx = Context(name=self.name)
        ctx.set(state)
        registry.register(ctx)
        return state
