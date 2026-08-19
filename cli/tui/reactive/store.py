"""
Reactive store — structured state with immutable update semantics.

For complex state (sessions, messages, parts), use a store instead of
individual signals. Updates go through a single `update(fn)` that receives
a mutable draft. Only observers of changed keys are notified.

    store = create_store({
        "status": "loading",
        "sessions": [],
        "messages": {},   # sessionID -> [Message]
    })

    store.update(lambda draft: draft.__setitem__("status", "ready"))
    store.subscribe("status", lambda v: print(f"status is {v}"))
"""

from __future__ import annotations

import copy
from typing import Any, Callable


class Store:
    """A reactive store with key-level observer notification."""

    def __init__(self, initial: dict[str, Any]) -> None:
        self._state: dict[str, Any] = copy.deepcopy(initial)
        self._subscribers: dict[str, list[Callable[[Any], None]]] = {}
        self._global_subscribers: list[Callable[[dict[str, Any]], None]] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        old = self._state.get(key)
        if old == value:
            return
        self._state[key] = value
        self._notify(key, value)

    def update(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Apply an immutable update. The draft is a deep copy; only changed
        keys trigger notifications."""
        draft = copy.deepcopy(self._state)
        fn(draft)
        for key in draft:
            if key not in self._state or self._state[key] != draft[key]:
                self._state[key] = draft[key]
                self._notify(key, draft[key])
        for key in list(self._state):
            if key not in draft:
                del self._state[key]
                self._notify(key, None)

    def state(self) -> dict[str, Any]:
        """Return a read-only snapshot of the current state."""
        return copy.deepcopy(self._state)

    def subscribe(self, key: str, fn: Callable[[Any], None]) -> Callable[[], None]:
        """Subscribe to changes on a specific key. Returns an unsubscribe fn."""
        self._subscribers.setdefault(key, []).append(fn)
        def _unsub() -> None:
            if key in self._subscribers:
                self._subscribers[key].remove(fn)
        return _unsub

    def subscribe_all(self, fn: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Subscribe to all state changes."""
        self._global_subscribers.append(fn)
        def _unsub() -> None:
            if fn in self._global_subscribers:
                self._global_subscribers.remove(fn)
        return _unsub

    def _notify(self, key: str, value: Any) -> None:
        for fn in self._subscribers.get(key, []):
            fn(value)
        for fn in self._global_subscribers:
            fn(self._state)


def create_store(initial: dict[str, Any]) -> Store:
    """Create a reactive store from an initial state dict."""
    return Store(initial)
