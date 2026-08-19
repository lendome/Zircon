"""
Theming system — typed themes, semantic color keys, system theme generation,
syntax highlighting, and color utilities.

Components reference `theme.text`, `theme.border`, etc. — never raw hex values.
Themes support per-color dark/light variants and can be auto-generated from
the terminal's ANSI palette ("system" theme).
"""

from __future__ import annotations

from .colors import Color, tint, luminance, with_alpha, parse_hex, ansi_to_color, selected_foreground
from .theme import Theme, ThemeMode
from .resolver import resolve_theme, ThemeDef
from .system import generate_system_theme
from .themes import DEFAULT_THEMES, list_themes, get_theme
from .syntax import get_syntax_rules, generate_subtle_syntax, SyntaxRule
from .detection import detect_terminal_mode, ThemeModeDetector

__all__ = [
    "Color",
    "tint",
    "luminance",
    "with_alpha",
    "parse_hex",
    "ansi_to_color",
    "selected_foreground",
    "Theme",
    "ThemeMode",
    "resolve_theme",
    "ThemeDef",
    "generate_system_theme",
    "DEFAULT_THEMES",
    "list_themes",
    "get_theme",
    "get_syntax_rules",
    "generate_subtle_syntax",
    "SyntaxRule",
    "detect_terminal_mode",
    "ThemeModeDetector",
]
