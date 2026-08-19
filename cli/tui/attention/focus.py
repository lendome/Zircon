"""
Focus tracking — tracks whether the TUI is focused or blurred.

The renderer emits focus/blur events. The attention system uses this
to decide whether to fire notifications.

States: unknown | focused | blurred
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from ..reactive.signal import Signal, signal


class FocusState(str, Enum):
    UNKNOWN = "unknown"
    FOCUSED = "focused"
    BLURRED = "blurred"


class FocusTracker:
    """
    Tracks terminal focus state.

    The renderer calls focus() / blur() when the terminal gains/loses focus.
    The attention system reads state() to decide whether to notify.
    """

    def __init__(self) -> None:
        self._state: Signal[FocusState] = signal(FocusState.UNKNOWN)
        self._handlers: list[Callable[[FocusState], None]] = []

    @property
    def state(self) -> Signal[FocusState]:
        return self._state

    @property
    def current(self) -> FocusState:
        return self._state.get()

    def focus(self) -> None:
        """Called when the terminal gains focus."""
        self._state.set(FocusState.FOCUSED)
        self._notify_handlers(FocusState.FOCUSED)

    def blur(self) -> None:
        """Called when the terminal loses focus."""
        self._state.set(FocusState.BLURRED)
        self._notify_handlers(FocusState.BLURRED)

    def on_change(self, handler: Callable[[FocusState], None]) -> Callable[[], None]:
        """Register a focus state change handler. Returns unsubscribe."""
        self._handlers.append(handler)
        def _unsub() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)
        return _unsub

    def _notify_handlers(self, state: FocusState) -> None:
        for handler in self._handlers:
            try:
                handler(state)
            except Exception:
                pass
