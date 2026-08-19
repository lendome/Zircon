"""
Which-key discovery panel — shows available bindings for the current mode.

Helps users discover shortcuts without reading documentation. Supports
grouping, scrolling, and layout toggles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .definitions import Definitions
from .keymap import Keymap
from ..theming.theme import Theme


@dataclass
class WhichKeyEntry:
    """A single entry in the which-key panel."""

    binding_name: str
    key_sequence: str
    description: str
    group: str = ""


class WhichKeyPanel:
    """
    Renders a which-key style discovery panel.

    Shows all bindings for the current mode, grouped by context,
    with their key sequences and descriptions.
    """

    def __init__(self, keymap: Keymap, theme: Theme | None = None) -> None:
        self.keymap = keymap
        self.theme = theme
        self._scroll_offset = 0
        self._visible = False

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False
        self._scroll_offset = 0

    def toggle(self) -> None:
        self._visible = not self._visible

    @property
    def is_visible(self) -> bool:
        return self._visible

    def scroll_down(self, rows: int = 5) -> None:
        self._scroll_offset += rows

    def scroll_up(self, rows: int = 5) -> None:
        self._scroll_offset = max(0, self._scroll_offset - rows)

    def get_entries(self) -> list[WhichKeyEntry]:
        """Collect all entries for the current mode."""
        entries: list[WhichKeyEntry] = []
        for name, defn in Definitions.items():
            key_seqs = self.keymap.get_key_sequences(name)
            if not key_seqs:
                continue
            entries.append(WhichKeyEntry(
                binding_name=name,
                key_sequence=", ".join(key_seqs),
                description=defn.description,
            ))
        return entries

    def render(self) -> RenderableType:
        """Render the which-key panel."""
        if not self._visible:
            return Text("")

        entries = self.get_entries()
        visible = entries[self._scroll_offset:]

        table = Table(show_header=True, header_style="", show_lines=False, padding=(0, 1))
        table.add_column("Key", style="bold", no_wrap=True, width=20)
        table.add_column("Description", no_wrap=True)

        for entry in visible:
            table.add_row(entry.key_sequence, entry.description)

        title = "Keybindings"
        border_style = "dim"
        if self.theme is not None:
            border_style = self.theme.border_active.to_rich()

        return Panel(table, title=title, border_style=border_style, padding=(0, 0))
