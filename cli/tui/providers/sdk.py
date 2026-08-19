"""
SDKProvider — holds the transport connection to the backend daemon.

Equivalent of OpenCode's SDKProvider. This is the bridge between the TUI
and the agent: all agent operations go through the transport.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


class SDKProvider(Provider):
    name = "sdk"

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def provide(self, registry: ContextRegistry) -> Any:
        ctx = Context(name=self.name)
        ctx.set(self._transport)
        registry.register(ctx)
        return self._transport
