"""
Editor context provider — auto-attaches the current editor selection.

When the user has a file open in their editor (VS Code, Zed), the current
selection is automatically attached as context. It appears as a chip in
the prompt footer and is sent as a system reminder with the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..context import Context, ContextRegistry
from ..reactive.signal import Signal, signal, computed
from .base import Provider


@dataclass
class EditorSelection:
    """A file selection from the editor."""

    file: str = ""
    ranges: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_range(self) -> bool:
        return any(r.get("text") for r in self.ranges)


def format_editor_context(selection: EditorSelection | None) -> str | None:
    """Format an editor selection as a system reminder string."""
    if not selection or not selection.file:
        return None

    if not selection.ranges or not any(r.get("text") for r in selection.ranges):
        return (
            f'<system-reminder>Note: The user opened the file "{selection.file}". '
            f"This may or may not be relevant.</system-reminder>"
        )

    parts: list[str] = []
    for i, r in enumerate(selection.ranges):
        if not r.get("text"):
            continue
        prefix = f"Selection {i + 1}: " if len(selection.ranges) > 1 else ""
        label = r.get("label", f"line {r.get('start', '?')}-{r.get('end', '?')}")
        text = r["text"]
        parts.append(
            f"Note: The user selected {prefix}{label} from \"{selection.file}\". ```{text}```"
        )
    return f"<system-reminder>{' '.join(parts)} This may or may not be relevant.</system-reminder>"


class EditorContextProvider(Provider):
    name = "editor_context"

    def __init__(self) -> None:
        self._selection = signal(None)
        self._enabled = signal(True)
        self._dismissed_key = signal(None)

    def set_selection(self, selection: EditorSelection | None) -> None:
        self._selection.set(selection)

    def dismiss(self) -> None:
        sel = self._selection.get()
        if sel:
            self._dismissed_key.set((sel.file, tuple(r.get("label", "") for r in sel.ranges)))

    @property
    def selection_signal(self) -> Signal:
        return self._selection

    def provide(self, registry: ContextRegistry) -> Any:
        ctx = Context(name=self.name)
        ctx.set(self)
        registry.register(ctx)

        # Also register the formatted context as a computed signal
        formatted = computed(
            lambda: format_editor_context(self._selection.get())
            if self._enabled.get() and self._selection.get()
            and (self._selection.get().file, tuple(r.get("label", "") for r in self._selection.get().ranges)) != self._dismissed_key.get()
            else None
        )
        fmt_ctx = Context(name="editor_context_formatted")
        fmt_ctx.set(formatted)
        registry.register(fmt_ctx)

        return self
