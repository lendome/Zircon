"""
Theme resolver — resolves raw theme definitions (with dark/light per color)
into a flat Theme structure based on the current mode.

Theme definitions support:
  - per-color dark/light variants: {"primary": {"dark": "#7aa2f7", "light": "#2e5f9e"}}
  - references via defs: {"defs": {"bg": "#1a1b26"}, "theme": {"background": "$bg"}}
  - transparent backgrounds: "transparent"
  - ANSI codes: {"primary": "ansi(12)"} or {"primary": 12}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .colors import Color, parse_hex, ansi_to_color, luminance
from .theme import Theme, ThemeMode


@dataclass
class ThemeDef:
    """A raw theme definition with per-color dark/light variants."""

    name: str
    defs: dict[str, Color] = field(default_factory=dict)
    colors: dict[str, Any] = field(default_factory=dict)

    def resolve(self, mode: ThemeMode) -> Theme:
        """Resolve this definition into a flat Theme for the given mode."""
        resolved: dict[str, Any] = {"name": self.name, "mode": mode}
        for key, value in self.colors.items():
            if key == "thinking_opacity":
                resolved[key] = float(value) if not isinstance(value, dict) else float(value.get(mode.value, 0.5))
            else:
                resolved[key] = self._resolve_color(value, mode)
        amber = parse_hex("#e0a000" if mode == ThemeMode.DARK else "#a06f00")
        for key in (
            "primary",
            "secondary",
            "warning",
            "info",
            "border_active",
            "selected_list_item_text",
            "markdown_heading",
            "markdown_link",
            "syntax_keyword",
            "syntax_function",
            "syntax_type",
        ):
            resolved[key] = amber
        neutral = resolved.get("text", Color(220, 220, 220))
        for key in ("success", "markdown_code", "syntax_string"):
            resolved[key] = neutral
        return Theme(**resolved)

    def _resolve_color(self, value: Any, mode: ThemeMode) -> Color | float:
        if isinstance(value, (int, float)):
            return ansi_to_color(int(value))

        if isinstance(value, str):
            if value == "transparent":
                return Color(0, 0, 0, 0.0)
            if value.startswith("$"):
                return self.defs.get(value[1:], Color(128, 128, 128))
            if value.startswith("ansi("):
                code = int(value[4:-1])
                return ansi_to_color(code)
            return parse_hex(value)

        if isinstance(value, dict):
            # Per-mode variant: {"dark": "...", "light": "..."}
            mode_str = mode.value
            if mode_str in value:
                return self._resolve_color(value[mode_str], mode)
            return Color(128, 128, 128)

        return Color(128, 128, 128)


def resolve_theme(defn: ThemeDef, mode: ThemeMode = ThemeMode.DARK) -> Theme:
    """Resolve a ThemeDef into a flat Theme."""
    return defn.resolve(mode)
