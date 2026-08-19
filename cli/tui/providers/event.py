"""
Event provider — typed event system for backend-to-TUI communication.

Events from the backend are dispatched through a typed event context.
Components subscribe to specific event types:

  event.on("session.status", handler)
  event.on("message.part.updated", handler)
  event.on("tui.command.execute", handler)
  event.on("tui.toast.show", handler)

Events include a workspace identifier so the TUI can filter by workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class Event:
    """A typed event dispatched through the event system."""

    type: str
    data: Any = None
    workspace: str | None = None


class EventSystem:
    """
    Typed event bus for backend-to-TUI communication.

    Events are dispatched by type. Handlers receive the event and can
    filter by workspace.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[Event], None]]] = {}
        self._global_handlers: list[Callable[[Event], None]] = []

    def on(self, event_type: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to events of a specific type. Returns an unsubscribe fn."""
        self._handlers.setdefault(event_type, []).append(handler)
        def _unsub() -> None:
            if event_type in self._handlers:
                self._handlers[event_type].remove(handler)
        return _unsub

    def on_all(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        """Subscribe to all events."""
        self._global_handlers.append(handler)
        def _unsub() -> None:
            if handler in self._global_handlers:
                self._global_handlers.remove(handler)
        return _unsub

    def emit(self, event: Event) -> None:
        """Dispatch an event to all matching handlers."""
        for handler in self._handlers.get(event.type, []):
            try:
                handler(event)
            except Exception:
                pass
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception:
                pass

    def emit_type(self, event_type: str, data: Any = None, workspace: str | None = None) -> None:
        """Convenience: emit an event by type."""
        self.emit(Event(type=event_type, data=data, workspace=workspace))


class EventProvider(Provider):
    name = "event"

    def provide(self, registry: ContextRegistry) -> Any:
        event_system = EventSystem()
        ctx = Context(name=self.name)
        ctx.set(event_system)
        registry.register(ctx)
        return event_system
