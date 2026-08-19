"""
Built-in themes — a set of predefined themes plus the system theme.

Theme priority layering:
  defaults < custom file themes < generated system

list_themes() returns all available, with "system" included if generated.
"""

from __future__ import annotations

from typing import Any

from .theme import Theme, ThemeMode
from .resolver import ThemeDef, resolve_theme
from .colors import Color, parse_hex


def _make_def(name: str, **colors: Any) -> ThemeDef:
    """Convenience to build a ThemeDef. Extracts 'defs' if present."""
    defs = colors.pop("defs", None) or {}
    resolved_defs: dict[str, Color] = {}
    for k, v in defs.items():
        if isinstance(v, str) and v.startswith("#"):
            resolved_defs[k] = parse_hex(v)
        else:
            resolved_defs[k] = v
    return ThemeDef(name=name, defs=resolved_defs, colors=colors)


DEFAULT_THEMES: dict[str, ThemeDef] = {
    "tokyo-night": _make_def(
        "tokyo-night",
        defs={},
        primary={"dark": "#7aa2f7", "light": "#2e5f9e"},
        secondary={"dark": "#bb9af7", "light": "#6b3fa0"},
        error={"dark": "#f7768e", "light": "#c4413a"},
        warning={"dark": "#e0af68", "light": "#a07a2e"},
        success={"dark": "#9ece6a", "light": "#4a7a2e"},
        info={"dark": "#7aa2f7", "light": "#2e5f9e"},
        text={"dark": "#c0caf5", "light": "#1a1b26"},
        text_muted={"dark": "#5a6280", "light": "#8a8aa0"},
        background="transparent",
        background_panel={"dark": "#1a1b26", "light": "#e1e2eb"},
        background_element={"dark": "#242534", "light": "#d0d1de"},
        background_menu={"dark": "#16171e", "light": "#e8e9f2"},
        border={"dark": "#5a6280", "light": "#a0a0b0"},
        border_active={"dark": "#7aa2f7", "light": "#2e5f9e"},
        border_subtle={"dark": "#2d3040", "light": "#c8c9d6"},
        selected_list_item_text={"dark": "#7aa2f7", "light": "#2e5f9e"},
        diff_added={"dark": "#9ece6a", "light": "#4a7a2e"},
        diff_removed={"dark": "#f7768e", "light": "#c4413a"},
        diff_context={"dark": "#969696", "light": "#888888"},
        diff_added_bg={"dark": "#283a28", "light": "#d8e8d0"},
        diff_removed_bg={"dark": "#3a2828", "light": "#e8d0d0"},
        markdown_heading={"dark": "#7aa2f7", "light": "#2e5f9e"},
        markdown_code={"dark": "#a6d694", "light": "#4a7a2e"},
        markdown_link={"dark": "#73c5f7", "light": "#2e7ab0"},
        syntax_comment={"dark": "#5c6382", "light": "#8a8aa0"},
        syntax_keyword={"dark": "#bb9af7", "light": "#6b3fa0"},
        syntax_function={"dark": "#7aa2f7", "light": "#2e5f9e"},
        syntax_string={"dark": "#a6d694", "light": "#4a7a2e"},
        syntax_number={"dark": "#f9a98a", "light": "#c0703a"},
        syntax_type={"dark": "#e6c080", "light": "#a08030"},
        thinking_opacity=0.5,
    ),
    "catppuccin": _make_def(
        "catppuccin",
        primary={"dark": "#89b4fa", "light": "#1e66f5"},
        secondary={"dark": "#cba6f7", "light": "#8839ef"},
        error={"dark": "#f38ba8", "light": "#d20f39"},
        warning={"dark": "#fab387", "light": "#df8e1d"},
        success={"dark": "#a6e3a1", "light": "#40a02b"},
        info={"dark": "#89b4fa", "light": "#1e66f5"},
        text={"dark": "#cdd6f4", "light": "#4c4f69"},
        text_muted={"dark": "#7f849c", "light": "#9ca0b0"},
        background="transparent",
        background_panel={"dark": "#1e1e2e", "light": "#eff1f5"},
        background_element={"dark": "#313244", "light": "#dce0e8"},
        background_menu={"dark": "#181825", "light": "#e6e9ef"},
        border={"dark": "#7f849c", "light": "#9ca0b0"},
        border_active={"dark": "#89b4fa", "light": "#1e66f5"},
        border_subtle={"dark": "#45475a", "light": "#bcc0cc"},
        selected_list_item_text={"dark": "#89b4fa", "light": "#1e66f5"},
        diff_added={"dark": "#a6e3a1", "light": "#40a02b"},
        diff_removed={"dark": "#f38ba8", "light": "#d20f39"},
        diff_context={"dark": "#a6adc8", "light": "#8c90a0"},
        diff_added_bg={"dark": "#2a3a2a", "light": "#d0e8d0"},
        diff_removed_bg={"dark": "#3a2a2a", "light": "#e8d0d0"},
        markdown_heading={"dark": "#89b4fa", "light": "#1e66f5"},
        markdown_code={"dark": "#a6e3a1", "light": "#40a02b"},
        markdown_link={"dark": "#74c7ec", "light": "#04a5e5"},
        syntax_comment={"dark": "#7f849c", "light": "#9ca0b0"},
        syntax_keyword={"dark": "#cba6f7", "light": "#8839ef"},
        syntax_function={"dark": "#89b4fa", "light": "#1e66f5"},
        syntax_string={"dark": "#a6e3a1", "light": "#40a02b"},
        syntax_number={"dark": "#fab387", "light": "#df8e1d"},
        syntax_type={"dark": "#f9e2af", "light": "#df8e1d"},
        thinking_opacity=0.55,
    ),
    "gruvbox": _make_def(
        "gruvbox",
        primary={"dark": "#83a598", "light": "#076678"},
        secondary={"dark": "#d3869b", "light": "#8f3f71"},
        error={"dark": "#fb4934", "light": "#cc241d"},
        warning={"dark": "#fabd2f", "light": "#d79921"},
        success={"dark": "#b8bb26", "light": "#79740e"},
        info={"dark": "#83a598", "light": "#076678"},
        text={"dark": "#ebdbb2", "light": "#3c3836"},
        text_muted={"dark": "#928374", "light": "#7c6f64"},
        background="transparent",
        background_panel={"dark": "#282828", "light": "#fbf1c7"},
        background_element={"dark": "#3c3836", "light": "#ebdbb2"},
        background_menu={"dark": "#1d2021", "light": "#f9e8b0"},
        border={"dark": "#928374", "light": "#7c6f64"},
        border_active={"dark": "#83a598", "light": "#076678"},
        border_subtle={"dark": "#504945", "light": "#d5c4a1"},
        selected_list_item_text={"dark": "#83a598", "light": "#076678"},
        diff_added={"dark": "#b8bb26", "light": "#79740e"},
        diff_removed={"dark": "#fb4934", "light": "#cc241d"},
        diff_context={"dark": "#a89984", "light": "#928374"},
        diff_added_bg={"dark": "#3a3a2a", "light": "#e0e8c8"},
        diff_removed_bg={"dark": "#3a2a2a", "light": "#e8c8c8"},
        markdown_heading={"dark": "#83a598", "light": "#076678"},
        markdown_code={"dark": "#b8bb26", "light": "#79740e"},
        markdown_link={"dark": "#83a598", "light": "#076678"},
        syntax_comment={"dark": "#928374", "light": "#7c6f64"},
        syntax_keyword={"dark": "#d3869b", "light": "#8f3f71"},
        syntax_function={"dark": "#83a598", "light": "#076678"},
        syntax_string={"dark": "#b8bb26", "light": "#79740e"},
        syntax_number={"dark": "#fabd2f", "light": "#d79921"},
        syntax_type={"dark": "#fe8019", "light": "#af3a03"},
        thinking_opacity=0.5,
    ),
    "dracula": _make_def(
        "dracula",
        primary={"dark": "#bd93f9", "light": "#6272a4"},
        secondary={"dark": "#ff79c6", "light": "#d63384"},
        error={"dark": "#ff5555", "light": "#e03131"},
        warning={"dark": "#f1fa8c", "light": "#f59f00"},
        success={"dark": "#50fa7b", "light": "#2f9e44"},
        info={"dark": "#8be9fd", "light": "#1c7ed6"},
        text={"dark": "#f8f8f2", "light": "#282a36"},
        text_muted={"dark": "#6272a4", "light": "#9098b0"},
        background="transparent",
        background_panel={"dark": "#282a36", "light": "#f8f8f2"},
        background_element={"dark": "#44475a", "light": "#e8e8e0"},
        background_menu={"dark": "#21222c", "light": "#f0f0e8"},
        border={"dark": "#6272a4", "light": "#9098b0"},
        border_active={"dark": "#bd93f9", "light": "#6272a4"},
        border_subtle={"dark": "#383a4a", "light": "#d0d0c8"},
        selected_list_item_text={"dark": "#bd93f9", "light": "#6272a4"},
        diff_added={"dark": "#50fa7b", "light": "#2f9e44"},
        diff_removed={"dark": "#ff5555", "light": "#e03131"},
        diff_context={"dark": "#a0a0a0", "light": "#888888"},
        diff_added_bg={"dark": "#2a3a2a", "light": "#d8e8d0"},
        diff_removed_bg={"dark": "#3a2a2a", "light": "#e8d0d0"},
        markdown_heading={"dark": "#bd93f9", "light": "#6272a4"},
        markdown_code={"dark": "#50fa7b", "light": "#2f9e44"},
        markdown_link={"dark": "#8be9fd", "light": "#1c7ed6"},
        syntax_comment={"dark": "#6272a4", "light": "#9098b0"},
        syntax_keyword={"dark": "#ff79c6", "light": "#d63384"},
        syntax_function={"dark": "#bd93f9", "light": "#6272a4"},
        syntax_string={"dark": "#f1fa8c", "light": "#f59f00"},
        syntax_number={"dark": "#50fa7b", "light": "#2f9e44"},
        syntax_type={"dark": "#8be9fd", "light": "#1c7ed6"},
        thinking_opacity=0.5,
    ),
}


def list_themes(mode: ThemeMode = ThemeMode.DARK, include_system: bool = True) -> dict[str, Theme]:
    """Return all available themes, resolved for the given mode.

    Priority: defaults < system (if generated).
    """
    themes: dict[str, Theme] = {}
    for name, defn in DEFAULT_THEMES.items():
        themes[name] = resolve_theme(defn, mode)

    if include_system:
        from .system import generate_system_theme
        themes["system"] = generate_system_theme(mode=mode)

    return themes


def get_theme(name: str, mode: ThemeMode = ThemeMode.DARK) -> Theme:
    """Get a specific theme by name. Falls back to 'system'."""
    if name == "system":
        from .system import generate_system_theme
        return generate_system_theme(mode=mode)

    defn = DEFAULT_THEMES.get(name)
    if defn is None:
        from .system import generate_system_theme
        return generate_system_theme(mode=mode)
    return resolve_theme(defn, mode)
