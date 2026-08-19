"""
Provider base class — each provider handles one concern in the app shell.

A provider is a callable that receives the ContextRegistry, creates its
context(s), and returns the context value. The AppShell assembles providers
in order so each can depend on all prior contexts.
"""

from __future__ import annotations

from typing import Any, Callable

from ..context import Context, ContextRegistry


class Provider:
    """
    Base class for providers. Subclasses implement `provide()`.

    A provider creates one or more contexts and returns its primary value.
    The AppShell calls providers in order, passing the growing registry.
    """

    name: str

    def provide(self, registry: ContextRegistry) -> Any:
        raise NotImplementedError


class SimpleProvider(Provider):
    """A provider that just creates a single context with a factory."""

    def __init__(self, name: str, factory: Callable[[ContextRegistry], Any]) -> None:
        self.name = name
        self._factory = factory

    def provide(self, registry: ContextRegistry) -> Any:
        ctx = Context(name=self.name, factory=self._factory)
        registry.register(ctx)
        return registry.initialize(ctx)
