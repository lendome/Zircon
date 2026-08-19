"""
Prompt history provider — holds the PromptHistory instance.

Exposes prompt history to the prompt component for up/down navigation.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..prompt.history import PromptHistory
from .base import Provider


class PromptHistoryProvider(Provider):
    name = "prompt_history"

    def provide(self, registry: ContextRegistry) -> Any:
        history = PromptHistory()
        ctx = Context(name=self.name)
        ctx.set(history)
        registry.register(ctx)
        return history
