"""
Theme dataclass — the flat, resolved structure with semantic color keys.

Components reference `theme.text`, `theme.border`, etc. — never raw hex.
This is the resolved version (after dark/light selection). The raw
definitions with per-color dark/light variants live in ThemeDef (resolver.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .colors import Color


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True)
class Theme:
    """A fully resolved theme with semantic color keys."""

    name: str = "default"
    mode: ThemeMode = ThemeMode.DARK

    # Core colors
    primary: Color = field(default_factory=lambda: Color(224, 160, 0))
    secondary: Color = field(default_factory=lambda: Color(224, 160, 0))
    error: Color = field(default_factory=lambda: Color(247, 118, 118))
    warning: Color = field(default_factory=lambda: Color(224, 160, 0))
    success: Color = field(default_factory=lambda: Color(158, 187, 110))
    info: Color = field(default_factory=lambda: Color(224, 160, 0))

    # Text
    text: Color = field(default_factory=lambda: Color(192, 202, 245))
    text_muted: Color = field(default_factory=lambda: Color(90, 98, 128))

    # Backgrounds
    background: Color = field(default_factory=lambda: Color(26, 27, 38, 0.0))
    background_panel: Color = field(default_factory=lambda: Color(26, 27, 38))
    background_element: Color = field(default_factory=lambda: Color(36, 38, 52))
    background_menu: Color = field(default_factory=lambda: Color(22, 23, 34))

    # Borders
    border: Color = field(default_factory=lambda: Color(90, 98, 128))
    border_active: Color = field(default_factory=lambda: Color(224, 160, 0))
    border_subtle: Color = field(default_factory=lambda: Color(45, 48, 64))

    # Selection
    selected_list_item_text: Color = field(default_factory=lambda: Color(40, 42, 58))

    # Diff colors
    diff_added: Color = field(default_factory=lambda: Color(158, 187, 110))
    diff_removed: Color = field(default_factory=lambda: Color(247, 118, 118))
    diff_context: Color = field(default_factory=lambda: Color(150, 150, 150))
    diff_added_bg: Color = field(default_factory=lambda: Color(40, 60, 40, 0.3))
    diff_removed_bg: Color = field(default_factory=lambda: Color(60, 40, 40, 0.3))

    # Markdown colors
    markdown_heading: Color = field(default_factory=lambda: Color(224, 160, 0))
    markdown_code: Color = field(default_factory=lambda: Color(166, 218, 149))
    markdown_link: Color = field(default_factory=lambda: Color(224, 160, 0))

    # Syntax colors
    syntax_comment: Color = field(default_factory=lambda: Color(92, 99, 130))
    syntax_keyword: Color = field(default_factory=lambda: Color(224, 160, 0))
    syntax_function: Color = field(default_factory=lambda: Color(224, 160, 0))
    syntax_string: Color = field(default_factory=lambda: Color(166, 218, 149))
    syntax_number: Color = field(default_factory=lambda: Color(249, 169, 140))
    syntax_type: Color = field(default_factory=lambda: Color(224, 160, 0))

    # Special
    thinking_opacity: float = 0.5

    def to_rich_style(self, key: str) -> str:
        """Get a Rich-compatible style string for a semantic color key."""
        color = getattr(self, key, None)
        if isinstance(color, Color):
            return color.to_rich()
        return ""
