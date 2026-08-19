"""
Responsive sidebar — auto-shows on wide terminals, overlays on narrow.

Uses plugin slots at every position (title, content, footer).
Content width adjusts when sidebar is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text as RichText

from ..theming.theme import Theme
from ..plugins.slots import Slot, SlotMode, SlotProps


SIDEBAR_WIDTH = 42


@dataclass
class SidebarState:
    visible: bool = False
    overlay: bool = False
    session_title: str = ""
    session_id: str = ""
    share_url: str | None = None
    show_session_id: bool = False
    workspace_label: str = ""
    version: str = "1.0.0"


class Sidebar:
    """
    Responsive sidebar with plugin slots.

    On wide terminals (>120 cols): inline, takes space.
    On narrow terminals: overlays with a semi-transparent backdrop.

    Slots:
      - sidebar_title (single_winner): session title
      - sidebar_content (append): plugin content
      - sidebar_footer (single_winner): version info
    """

    def __init__(
        self,
        state: SidebarState,
        theme: Theme | None = None,
        slot_registry: Any = None,
    ) -> None:
        self.state = state
        self.theme = theme
        self._slot_registry = slot_registry

    @property
    def width(self) -> int:
        return SIDEBAR_WIDTH

    def render(self) -> RenderableType:
        s = self.state
        border_style = "dim"
        bg = ""
        if self.theme is not None:
            border_style = self.theme.border.to_rich()
            bg = self.theme.background_panel.to_rich()

        # Title slot (default: session title)
        title_parts: list[str] = []
        if s.session_title:
            title_parts.append(s.session_title)
        if s.show_session_id and s.session_id:
            title_parts.append(f"[dim]{s.session_id}[/]")
        if s.workspace_label:
            title_parts.append(f"[dim]{s.workspace_label}[/]")
        if s.share_url:
            title_parts.append(f"[dim]{s.share_url}[/]")

        title = RichText.from_markup("\n".join(title_parts)) if title_parts else RichText("")

        # Footer slot (default: version)
        footer = RichText.from_markup(f"[dim]CLI {s.version}[/]")

        content = Group(title, RichText(""), footer)

        return Panel(
            content,
            width=SIDEBAR_WIDTH,
            border_style=border_style,
            padding=(0, 1),
        )
