"""
DialogSelect — reusable fuzzy-filtered list dialog with categories,
footer hints, side actions, and custom rendering.

Features:
  - Fuzzy filtering by title
  - Category grouping
  - Footer hints (keyboard shortcuts)
  - Side actions (custom buttons)
  - on_move callback (preview on hover/selection change)
  - locked mode (view-only)
  - preserve_selection (keep selection when list updates)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText

from ..palette.fuzzy import fuzzy_score
from ..theming.theme import Theme


@dataclass
class DialogOption:
    """A single option in a DialogSelect."""

    title: str
    value: str = ""
    category: str = ""
    footer: str = ""
    gutter: str = ""
    selected: bool = False
    on_select: Callable[[Any], None] | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DialogAction:
    """A side action button in a DialogSelect."""

    command: str
    title: str
    side: str = "right"  # "left" | "right"
    on_trigger: Callable[[DialogOption | None], None] | None = None


@dataclass
class FooterHint:
    """A keyboard shortcut hint shown at the bottom."""

    title: str
    label: str
    side: str = "right"  # "left" | "right"


class DialogSelect:
    """
    A filterable list dialog.

    Usage:
        dialog = DialogSelect(
            title="Switch model",
            options=model_options,
            current=current_model,
            on_select=lambda opt: select_model(opt.value),
        )
    """

    def __init__(
        self,
        title: str = "",
        options: list[DialogOption] | None = None,
        current: str | None = None,
        on_select: Callable[[DialogOption], None] | None = None,
        on_move: Callable[[str], None] | None = None,
        actions: list[DialogAction] | None = None,
        footer_hints: list[FooterHint] | None = None,
        locked: bool = False,
        preserve_selection: bool = True,
        size: str = "medium",
        theme: Theme | None = None,
    ) -> None:
        self.title = title
        self.options = options or []
        self.current = current
        self._on_select = on_select
        self._on_move = on_move
        self.actions = actions or []
        self.footer_hints = footer_hints or [
            FooterHint(title="Select", label="Enter", side="right"),
            FooterHint(title="Cancel", label="Esc", side="right"),
        ]
        self.locked = locked
        self.preserve_selection = preserve_selection
        self.size = size
        self.theme = theme
        self._filter: str = ""
        self._selected_index: int = 0
        self._filtered: list[DialogOption] = []

    @property
    def width(self) -> int:
        sizes = {"medium": 60, "large": 88, "xlarge": 116}
        return sizes.get(self.size, 60)

    def set_filter(self, text: str) -> None:
        self._filter = text
        self._apply_filter()
        if not self.preserve_selection or self._selected_index >= len(self._filtered):
            self._selected_index = 0

    def _apply_filter(self) -> None:
        if not self._filter:
            self._filtered = list(self.options)
        else:
            scored: list[tuple[DialogOption, float]] = []
            for opt in self.options:
                score = fuzzy_score(self._filter, opt.title)
                if score > 0:
                    score += fuzzy_score(self._filter, opt.category) * 0.3
                    scored.append((opt, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            self._filtered = [opt for opt, _ in scored]

    def move_up(self) -> None:
        if self._filtered:
            self._selected_index = max(0, self._selected_index - 1)
            self._fire_move()

    def move_down(self) -> None:
        if self._filtered:
            self._selected_index = min(len(self._filtered) - 1, self._selected_index + 1)
            self._fire_move()

    def select(self) -> bool:
        """Execute the selected option's callback."""
        if self.locked or not self._filtered:
            return False
        if self._selected_index >= len(self._filtered):
            return False
        opt = self._filtered[self._selected_index]
        if self._on_select is not None:
            self._on_select(opt)
        elif opt.on_select is not None:
            opt.on_select(opt)
        return True

    def trigger_action(self, action: DialogAction) -> None:
        """Trigger a side action."""
        opt = self._filtered[self._selected_index] if self._filtered else None
        if action.on_trigger is not None:
            action.on_trigger(opt)

    def _fire_move(self) -> None:
        if self._on_move is not None and 0 <= self._selected_index < len(self._filtered):
            self._on_move(self._filtered[self._selected_index].value)

    def render(self) -> RenderableType:
        """Render the dialog."""
        if not self._filtered:
            self._apply_filter()

        border_style = "dim"
        if self.theme is not None:
            border_style = self.theme.border_active.to_rich()

        table = Table(show_header=False, show_lines=False, padding=(0, 1))
        table.add_column("Gutter", width=2, no_wrap=True)
        table.add_column("Title", no_wrap=True)
        table.add_column("Category", style="dim", no_wrap=True, width=12)

        current_category: str | None = None
        for i, opt in enumerate(self._filtered):
            is_selected = i == self._selected_index
            prefix = ">" if is_selected else " "
            row_style = ""
            if is_selected and self.theme is not None:
                row_style = f"on {self.theme.background_element.to_rich()}"

            table.add_row(
                RichText(f"{prefix}{opt.gutter}", style="dim"),
                RichText(opt.title, style="bold" if is_selected else ""),
                RichText(opt.category, style="dim"),
                style=row_style,
            )

        # Footer hints
        left_hints = [h for h in self.footer_hints if h.side == "left"]
        right_hints = [h for h in self.footer_hints if h.side == "right"]
        footer_parts: list[str] = []
        for h in left_hints:
            footer_parts.append(f"[dim]{h.title}[/] [bold]{h.label}[/]")
        for h in right_hints:
            footer_parts.append(f"[dim]{h.title}[/] [bold]{h.label}[/]")

        footer = "  ".join(footer_parts)

        header = self.title
        if self._filter:
            header += f"  [dim]{self._filter}[/]"

        children: list[RenderableType] = [table]
        if footer:
            children.append(RichText.from_markup(footer))

        return Panel(
            Group(*children),
            title=header,
            border_style=border_style,
            width=self.width,
            padding=(0, 0),
        )
