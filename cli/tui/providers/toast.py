"""
ToastProvider — transient notification messages.

Equivalent of OpenCode's ToastProvider. Buffers toast messages that
components can push and the renderer can display.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class Toast:
    message: str
    style: str = "info"
    duration: float = 3.0


@dataclass
class ToastState:
    toasts: list[Toast] = field(default_factory=list)

    def push(self, message: str, style: str = "info", duration: float = 3.0) -> None:
        self.toasts.append(Toast(message=message, style=style, duration=duration))

    def pop(self) -> Toast | None:
        if self.toasts:
            return self.toasts.pop(0)
        return None

    def clear(self) -> None:
        self.toasts.clear()


class ToastProvider(Provider):
    name = "toast"

    def provide(self, registry: ContextRegistry) -> Any:
        state = ToastState()
        ctx = Context(name=self.name)
        ctx.set(state)
        registry.register(ctx)
        return state
