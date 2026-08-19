"""
PluginRuntimeProvider — initializes the plugin host and slot registry.

Provides the PluginHost and PluginRuntime (with slots, routes, commands)
to the TUI. The CLI uses NoOpPluginHost by default (no plugins loaded).
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..plugins.host import PluginHost
from ..plugins.api import create_tui_api
from ..plugins.slots import Slot
from .base import Provider


class PluginRuntimeProvider(Provider):
    name = "plugin_runtime"

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = workspace

    def provide(self, registry: ContextRegistry) -> Any:
        host = PluginHost()
        ctx = Context(name=self.name)
        ctx.set(host)
        registry.register(ctx)
        return host
