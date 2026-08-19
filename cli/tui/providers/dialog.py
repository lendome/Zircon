"""
Dialog provider — stack-based modal dialog management.

Dialogs can be pushed, replaced, or cleared. When any dialog is open,
a "modal" mode is pushed onto the keymap stack so dialog bindings take
priority. Focus is saved and restored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import RenderableType

from ..context import Context, ContextRegistry
from ..reactive.signal import Signal, signal, computed
from .base import Provider


@dataclass
class DialogEntry:
    """A single dialog on the stack."""

    renderable: RenderableType | None = None
    on_close: Callable[[], None] | None = None
    on_select: Callable[[Any], None] | None = None
    title: str = ""


class DialogManager:
    """
    Stack-based dialog management.

    - push(): add a dialog on top of the stack
    - replace(): close all dialogs and open a new one
    - clear(): close all dialogs
    - pop(): close the top dialog

    When dialogs are open, the stack length > 0, and a modal mode
    should be pushed on the keymap.
    """

    def __init__(self) -> None:
        self._stack: list[DialogEntry] = []
        self._stack_signal: Signal[int] = signal(0)
        self._saved_focus: Any | None = None

    @property
    def stack_signal(self) -> Signal[int]:
        return self._stack_signal

    @property
    def stack(self) -> list[DialogEntry]:
        return list(self._stack)

    @property
    def top(self) -> DialogEntry | None:
        if self._stack:
            return self._stack[-1]
        return None

    @property
    def is_open(self) -> bool:
        return len(self._stack) > 0

    def push(self, entry: DialogEntry) -> None:
        """Push a dialog on top of the stack."""
        self._stack.append(entry)
        self._stack_signal.set(len(self._stack))

    def replace(self, entry: DialogEntry) -> None:
        """Close all dialogs and open a new one."""
        for item in self._stack:
            if item.on_close is not None:
                item.on_close()
        self._stack = [entry]
        self._stack_signal.set(1)

    def pop(self) -> DialogEntry | None:
        """Close the top dialog."""
        if not self._stack:
            return None
        entry = self._stack.pop()
        if entry.on_close is not None:
            entry.on_close()
        self._stack_signal.set(len(self._stack))
        return entry

    def clear(self) -> None:
        """Close all dialogs."""
        for item in self._stack:
            if item.on_close is not None:
                item.on_close()
        self._stack = []
        self._stack_signal.set(0)

    def save_focus(self, focus: Any) -> None:
        """Save the currently focused element before opening a dialog."""
        if not self._stack:
            self._saved_focus = focus

    def restore_focus(self) -> Any | None:
        """Restore focus to the saved element after closing dialogs."""
        if not self._stack:
            focus = self._saved_focus
            self._saved_focus = None
            return focus
        return None


class DialogProvider(Provider):
    name = "dialog"

    def provide(self, registry: ContextRegistry) -> Any:
        manager = DialogManager()
        ctx = Context(name=self.name)
        ctx.set(manager)
        registry.register(ctx)
        return manager
