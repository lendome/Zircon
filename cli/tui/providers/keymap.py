"""
KeymapProvider — holds the Keymap and InputBindings for the TUI.

Equivalent of OpenCode's OpencodeKeymapProvider. Provides the keymap
instance, input bindings, and which-key panel to all components.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..keymap.keymap import Keymap
from ..keymap.input_bindings import InputBindings
from ..keymap.which_key import WhichKeyPanel
from .base import Provider


class KeymapProvider(Provider):
    name = "keymap"

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = overrides or {}

    def provide(self, registry: ContextRegistry) -> Any:
        keymap = Keymap(overrides=self._overrides)
        input_bindings = InputBindings()

        ctx = Context(name=self.name)
        ctx.set(keymap)
        registry.register(ctx)

        ib_ctx = Context(name="input_bindings")
        ib_ctx.set(input_bindings)
        registry.register(ib_ctx)

        # Which-key panel needs a theme, but we create it lazily
        # (it will be initialized after ThemeProvider runs)
        wk_ctx = Context(name="which_key")
        wk_ctx.set(None)
        registry.register(wk_ctx)

        return keymap
