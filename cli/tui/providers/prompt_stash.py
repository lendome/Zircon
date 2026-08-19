"""
Prompt stash provider — holds the PromptStash instance.

Enables saving and restoring prompt drafts across route changes.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..prompt.stash import PromptStash
from .base import Provider


class PromptStashProvider(Provider):
    name = "prompt_stash"

    def provide(self, registry: ContextRegistry) -> Any:
        stash = PromptStash()
        ctx = Context(name=self.name)
        ctx.set(stash)
        registry.register(ctx)
        return stash
