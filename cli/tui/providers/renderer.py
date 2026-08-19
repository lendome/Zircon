"""
RendererProvider — holds the terminal renderer and dimensions signal.

Equivalent of OpenCode's TuiTerminalEnvironmentProvider. Provides the
Renderer instance and a reactive terminal dimensions signal to all
components.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..renderer.renderer import Renderer, RendererConfig
from ..renderer.dimensions import use_terminal_dimensions
from .base import Provider


class RendererProvider(Provider):
    name = "renderer"

    def __init__(self, config: RendererConfig | None = None) -> None:
        self._config = config or RendererConfig()

    def provide(self, registry: ContextRegistry) -> Any:
        renderer = Renderer(self._config)
        dimensions = use_terminal_dimensions()

        ctx = Context(name=self.name)
        ctx.set(renderer)
        registry.register(ctx)

        dim_ctx = Context(name="dimensions")
        dim_ctx.set(dimensions)
        registry.register(dim_ctx)

        return renderer
