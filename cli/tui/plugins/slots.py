"""
Slot system — named insertion points where plugins can render content.

Modes:
  - replace:       plugin replaces default content entirely
  - single_winner: one plugin wins (highest priority), default if none
  - append:        all plugins' content stacks in order

Slots are reactive — when plugins register/unregister, the slot re-renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.text import Text as RichText


class SlotMode(str, Enum):
    REPLACE = "replace"
    SINGLE_WINNER = "single_winner"
    APPEND = "append"


@dataclass
class SlotProps:
    """Props passed to a slot and its registered render functions."""

    name: str
    session_id: str | None = None
    title: str | None = None
    visible: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SlotRegistration:
    """A plugin's registration for a specific slot."""

    plugin_id: str
    slot_name: str
    render_fn: Callable[[SlotProps], RenderableType]
    priority: int = 0


class Slot:
    """
    A named insertion point in the UI.

    Usage:
        Slot(name="home_logo", mode=SlotMode.REPLACE, default=Logo())
        Slot(name="session_prompt", mode=SlotMode.REPLACE, props=...)
        Slot(name="sidebar_content", mode=SlotMode.APPEND)
    """

    def __init__(
        self,
        name: str,
        mode: SlotMode = SlotMode.REPLACE,
        props: SlotProps | None = None,
        default: RenderableType | None = None,
        registry: "SlotRegistry | None" = None,
    ) -> None:
        self.name = name
        self.mode = mode
        self.props = props or SlotProps(name=name)
        self.default = default
        self._registry = registry

    def render(self) -> RenderableType:
        """Render the slot — uses plugin content or the default."""
        if self._registry is None:
            return self.default or RichText("")

        registrations = self._registry.get_registrations(self.name)

        if not registrations:
            return self.default or RichText("")

        if self.mode == SlotMode.REPLACE:
            # Highest priority wins, or default if none
            best = max(registrations, key=lambda r: r.priority)
            try:
                return best.render_fn(self.props)
            except Exception:
                return self.default or RichText("")

        elif self.mode == SlotMode.SINGLE_WINNER:
            best = max(registrations, key=lambda r: r.priority)
            try:
                return best.render_fn(self.props)
            except Exception:
                return self.default or RichText("")

        elif self.mode == SlotMode.APPEND:
            # All registrations stack in priority order
            sorted_regs = sorted(registrations, key=lambda r: r.priority)
            parts: list[RenderableType] = []
            for reg in sorted_regs:
                try:
                    parts.append(reg.render_fn(self.props))
                except Exception:
                    pass  # plugin error — skip, don't crash
            if parts:
                return Group(*parts)
            return self.default or RichText("")

        return self.default or RichText("")
