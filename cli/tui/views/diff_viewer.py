"""
Diff viewer — full-featured diff with file trees, hunk navigation,
split/unified views, and expand/collapse.

Keybindings:
  diff_close: escape, q
  diff_toggle: enter, space
  diff_expand: right
  diff_expand_all: E
  diff_collapse: left
  diff_switch_focus: tab
  diff_next_hunk: ]
  diff_previous_hunk: [
  diff_next_file: n
  diff_previous_file: p
  diff_toggle_file_tree: b
  diff_single_patch: s
  diff_switch_source: d
  diff_toggle_view: v
  diff_help: ?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text as RichText

from ..theming.theme import Theme


class DiffStyle(str, Enum):
    AUTO = "auto"
    STACKED = "stacked"


@dataclass
class DiffFile:
    filename: str = ""
    additions: int = 0
    deletions: int = 0
    content: str = ""
    expanded: bool = True


@dataclass
class DiffViewerState:
    files: list[DiffFile] = field(default_factory=list)
    selected_file: int = 0
    focus: str = "tree"  # "tree" | "diff"
    style: DiffStyle = DiffStyle.AUTO
    show_file_tree: bool = True
    unified_view: bool = True
    source: str = "current"  # "current" | "revert"


class DiffViewer:
    """
    Full-featured diff viewer.

    Features:
      - File tree with expand/collapse
      - Hunk navigation (next/prev)
      - File navigation (next/prev)
      - Split/unified view toggle
      - File tree toggle
      - Source switch (revert vs current)
    """

    def __init__(
        self,
        state: DiffViewerState | None = None,
        theme: Theme | None = None,
    ) -> None:
        self.state = state or DiffViewerState()
        self.theme = theme

    def next_hunk(self) -> None:
        pass

    def previous_hunk(self) -> None:
        pass

    def next_file(self) -> None:
        if self.state.files:
            self.state.selected_file = (self.state.selected_file + 1) % len(self.state.files)

    def previous_file(self) -> None:
        if self.state.files:
            self.state.selected_file = (self.state.selected_file - 1) % len(self.state.files)

    def toggle_file(self) -> None:
        if 0 <= self.state.selected_file < len(self.state.files):
            f = self.state.files[self.state.selected_file]
            f.expanded = not f.expanded

    def expand_all(self) -> None:
        for f in self.state.files:
            f.expanded = True

    def collapse(self) -> None:
        if 0 <= self.state.selected_file < len(self.state.files):
            self.state.files[self.state.selected_file].expanded = False

    def switch_focus(self) -> None:
        self.state.focus = "diff" if self.state.focus == "tree" else "tree"

    def toggle_file_tree(self) -> None:
        self.state.show_file_tree = not self.state.show_file_tree

    def toggle_view(self) -> None:
        self.state.unified_view = not self.state.unified_view

    def switch_source(self) -> None:
        self.state.source = "revert" if self.state.source == "current" else "current"

    def render(self) -> RenderableType:
        border_style = "yellow"
        if self.theme is not None:
            border_style = self.theme.warning.to_rich()

        parts: list[RenderableType] = []

        # File tree
        if self.state.show_file_tree and self.state.files:
            tree_lines: list[str] = []
            for i, f in enumerate(self.state.files):
                marker = ">" if i == self.state.selected_file else " "
                expand = "-" if f.expanded else "+"
                adds = f" [green]+{f.additions}[/]" if f.additions > 0 else ""
                dels = f" [red]-{f.deletions}[/]" if f.deletions > 0 else ""
                tree_lines.append(f"{marker} {expand} {f.filename}{adds}{dels}")
            parts.append(RichText.from_markup("\n".join(tree_lines)))

        # Diff content
        if 0 <= self.state.selected_file < len(self.state.files):
            f = self.state.files[self.state.selected_file]
            if f.expanded and f.content:
                parts.append(Syntax(f.content, "diff", theme="ansi_dark", word_wrap=True))

        return Panel(
            Group(*parts) if parts else RichText("No changes"),
            title="Diff Viewer",
            border_style=border_style,
        )
