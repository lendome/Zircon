"""
ArgsProvider — holds parsed CLI args accessible throughout the TUI.

Equivalent of OpenCode's ArgsProvider.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


class ArgsProvider(Provider):
    name = "args"

    def __init__(self, args: dict[str, Any]) -> None:
        self._args = args

    def provide(self, registry: ContextRegistry) -> Any:
        ctx = Context(name=self.name)
        ctx.set(self._args)
        registry.register(ctx)
        return self._args
