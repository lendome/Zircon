"""
AttentionProvider — holds the AttentionManager instance.

Provides focus tracking, OS notifications, and sound packs to the TUI.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..attention.manager import AttentionManager, AttentionConfig
from .base import Provider


class AttentionProvider(Provider):
    name = "attention"

    def __init__(self, config: AttentionConfig | None = None) -> None:
        self._config = config or AttentionConfig()

    def provide(self, registry: ContextRegistry) -> Any:
        manager = AttentionManager(config=self._config)
        ctx = Context(name=self.name)
        ctx.set(manager)
        registry.register(ctx)
        return manager
