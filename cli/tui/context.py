"""
Context system — the Python equivalent of SolidJS createContext / useContext.

Each provider registers a Context with a factory function. The AppShell
builds the provider tree in order, passing each provider access to all
previously-created contexts. This mirrors OpenCode's createSimpleContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Context(Generic[T]):
    """
    A named context slot holding a single value.

    Created via create_context("name", factory). The factory is called
    during app shell construction with access to all prior contexts.
    """

    name: str
    factory: Callable[[ContextRegistry], T] | None = None
    _value: Any = None
    _initialized: bool = False

    def get(self) -> T:
        if not self._initialized:
            raise RuntimeError(f"Context '{self.name}' accessed before initialization")
        return self._value  # type: ignore[no-any-return]

    def set(self, value: T) -> None:
        self._value = value
        self._initialized = True


class ContextRegistry:
    """
    Holds all contexts for the current app instance.

    Providers register contexts here; consumers look them up by name.
    The registry is passed to each provider's factory so it can depend
    on earlier providers (dependency ordering is enforced by the provider
    tree in app.py).
    """

    def __init__(self) -> None:
        self._contexts: dict[str, Context[Any]] = {}

    def register(self, context: Context[T]) -> Context[T]:
        self._contexts[context.name] = context
        return context

    def get(self, name: str) -> Any:
        ctx = self._contexts.get(name)
        if ctx is None:
            raise KeyError(f"No context registered as '{name}'")
        return ctx.get()

    def has(self, name: str) -> bool:
        ctx = self._contexts.get(name)
        return ctx is not None and ctx._initialized

    def initialize(self, context: Context[T]) -> T:
        """Call a context's factory and store the result."""
        if context.factory is not None:
            value = context.factory(self)
        else:
            value = None  # type: ignore[assignment]
        context.set(value)
        return value


def create_context(
    name: str,
    factory: Callable[[ContextRegistry], T] | None = None,
) -> Context[T]:
    """Create a context with an optional factory function."""
    return Context(name=name, factory=factory)


def use_context(registry: ContextRegistry, name: str) -> Any:
    """Look up a context by name from the registry."""
    return registry.get(name)
