"""
Command palette dialog — fuzzy-searchable list of all commands.

Features:
  - Fuzzy filtering by title/category/name
  - Suggested commands surface first when no filter
  - Category grouping
  - Keyboard shortcut display in footer
  - Programmatic dispatch on select
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText

from .fuzzy import fuzzy_score
from .registry import CommandRegistry, CommandEntry
from ..theming.theme import Theme


@dataclass
class PaletteOption:
    """A single option in the palette."""

    title: str
    category: str
    value: str
    footer: str = ""
    suggested: bool = False
    on_select: Callable[[], None] | None = None


class CommandPalette:
    """
    The command palette — a filterable list of all commands.

    Opens with Ctrl+P. Shows all registered commands grouped by category,
    with suggested commands surfaced first when no filter is applied.
    Fuzzy search filters the list as the user types.
    """

    _MAX_VISIBLE_OPTIONS = 10

    def __init__(
        self,
        registry: CommandRegistry,
        theme: Theme | None = None,
    ) -> None:
        self.registry = registry
        self.theme = theme
        self._filter: str = ""
        self._selected_index: int = 0
        self._visible_start: int = 0
        self._visible: bool = False
        self._options: list[PaletteOption] = []

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def filter(self) -> str:
        return self._filter

    def show(self) -> None:
        self._visible = True
        self._filter = ""
        self._selected_index = 0
        self._visible_start = 0
        self._rebuild_options()

    def hide(self) -> None:
        self._visible = False
        self._filter = ""
        self._options = []

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def set_filter(self, text: str) -> None:
        self._filter = text
        self._selected_index = 0
        self._visible_start = 0
        self._rebuild_options()

    def move_up(self) -> None:
        if self._options:
            self._selected_index = max(0, self._selected_index - 1)
            self._ensure_selection_visible()

    def move_down(self) -> None:
        if self._options:
            self._selected_index = min(len(self._options) - 1, self._selected_index + 1)
            self._ensure_selection_visible()

    def _ensure_selection_visible(self) -> None:
        """Keep the selected option inside the rendered viewport."""
        max_start = max(0, len(self._options) - self._MAX_VISIBLE_OPTIONS)
        if self._selected_index < self._visible_start:
            self._visible_start = self._selected_index
        elif self._selected_index >= self._visible_start + self._MAX_VISIBLE_OPTIONS:
            self._visible_start = self._selected_index - self._MAX_VISIBLE_OPTIONS + 1
        self._visible_start = min(self._visible_start, max_start)

    def select(self) -> bool:
        """Execute the selected command. Returns True if a command was run."""
        if not self._visible or not self._options:
            return False
        if self._selected_index >= len(self._options):
            return False
        opt = self._options[self._selected_index]
        self.hide()
        if opt.on_select is not None:
            opt.on_select()
        return True

    def _rebuild_options(self) -> None:
        """Rebuild the options list based on the current filter."""
        entries = self.registry.get_entries(namespace="palette")

        if self._filter:
            # Fuzzy search — rank all commands by score
            scored: list[tuple[PaletteOption, float]] = []
            for entry in entries:
                opt = self._entry_to_option(entry)
                score = fuzzy_score(self._filter, opt.title)
                if score > 0:
                    score += fuzzy_score(self._filter, opt.category) * 0.3
                    scored.append((opt, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            self._options = [opt for opt, _ in scored]
        else:
            # No filter — suggested first, then by category
            options = [self._entry_to_option(e) for e in entries]
            suggested = [o for o in options if o.suggested]
            rest = [o for o in options if not o.suggested]
            suggested = [
                PaletteOption(
                    title=o.title,
                    category="Suggested",
                    value=f"suggested:{o.value}",
                    footer=o.footer,
                    suggested=True,
                    on_select=o.on_select,
                )
                for o in suggested
            ]
            self._options = suggested + rest

    def _entry_to_option(self, entry: CommandEntry) -> PaletteOption:
        cmd = entry.command
        return PaletteOption(
            title=cmd.title or cmd.name,
            category=cmd.category,
            value=cmd.name,
            footer=entry.footer,
            suggested=cmd.is_suggested(),
            on_select=lambda c=cmd: self.registry.dispatch(c.name),
        )

    def render(self) -> RenderableType:
        """Render the palette dialog."""
        if not self._visible:
            return RichText("")

        # The injected theme is a reactive signal (Signal/_Computed[Theme]);
        # resolve it at render time so dark/light mode switches are honored.
        theme = self.theme
        if theme is not None and hasattr(theme, "get"):
            theme = theme.get()

        border_style = "cyan"
        if theme is not None:
            border_style = theme.border_active.to_rich()

        table = Table(show_header=True, header_style="dim", show_lines=False, padding=(0, 1))
        table.add_column("Title", no_wrap=True)
        table.add_column("Category", style="dim", no_wrap=True, width=12)
        table.add_column("Shortcut", style="dim", no_wrap=True, width=10)

        current_category: str | None = None
        self._ensure_selection_visible()
        visible_end = min(
            len(self._options),
            self._visible_start + self._MAX_VISIBLE_OPTIONS,
        )
        for i in range(self._visible_start, visible_end):
            opt = self._options[i]
            is_selected = i == self._selected_index
            prefix = "> " if is_selected else "  "
            style = "bold" if is_selected else ""

            # Show category header when it changes (non-suggested only)
            if opt.category != current_category and opt.category != "Suggested":
                current_category = opt.category

            row_style = ""
            if is_selected and theme is not None:
                row_style = f"on {theme.background_element.to_rich()}"

            table.add_row(
                RichText(f"{prefix}{opt.title}", style=style),
                RichText(opt.category, style="dim"),
                RichText(opt.footer, style="dim"),
                style=row_style,
            )

        header = f"Command Palette  [dim]{self._filter or 'type to search...'}[/]"
        return Panel(table, title=header, border_style=border_style, padding=(0, 0))
