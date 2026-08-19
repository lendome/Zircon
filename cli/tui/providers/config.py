"""
ConfigProvider — holds TUI configuration (keymaps, display options).

Equivalent of OpenCode's TuiConfigProvider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class TuiConfig:
    show_reasoning: bool = True
    show_tool_calls: bool = True
    show_thinking: bool = True
    max_tool_result_lines: int = 20
    refresh_rate: int = 12
    paste_threshold: int = 1000


class ConfigProvider(Provider):
    name = "config"

    def provide(self, registry: ContextRegistry) -> Any:
        config = TuiConfig()
        ctx = Context(name=self.name)
        ctx.set(config)
        registry.register(ctx)
        return config
