"""
System theme generation — auto-generates a theme from the terminal's
ANSI color palette so colors always match the terminal.

Reads the terminal's default foreground/background and derives a complete
theme: grays, muted text, diff colors (blended into the background), and
syntax colors from the ANSI palette.
"""

from __future__ import annotations

from typing import Any

from .colors import (
    Color, parse_hex, luminance, generate_gray_scale,
    generate_muted_text, tint, ansi_to_color,
)
from .theme import Theme, ThemeMode
from .resolver import ThemeDef


def detect_mode_from_bg(bg: Color) -> ThemeMode:
    """Determine dark/light mode from a background color."""
    return ThemeMode.DARK if luminance(bg) < 0.5 else ThemeMode.LIGHT


def generate_system_theme(
    terminal_colors: dict[str, Any] | None = None,
    mode: ThemeMode | None = None,
) -> Theme:
    """Generate a complete theme from the terminal's ANSI palette.

    Args:
        terminal_colors: dict with keys like "default_background",
                         "default_foreground", and optionally specific
                         ANSI color codes (0-255).
        mode: override the detected mode (otherwise derived from bg)
    """
    tc = terminal_colors or {}

    bg_hex = tc.get("default_background", "#1a1b26")
    fg_hex = tc.get("default_foreground", "#c0caf5")

    bg = parse_hex(bg_hex) if isinstance(bg_hex, str) else bg_hex
    fg = parse_hex(fg_hex) if isinstance(fg_hex, str) else fg_hex

    detected_mode = mode or detect_mode_from_bg(bg)
    is_dark = detected_mode == ThemeMode.DARK

    grays = generate_gray_scale(bg, is_dark)
    text_muted = generate_muted_text(bg, is_dark)

    # Derive diff colors by blending ANSI green/red into the background
    ansi_green = ansi_to_color(tc.get("green", 2))
    ansi_red = ansi_to_color(tc.get("red", 1))
    diff_alpha = 0.15 if is_dark else 0.25
    diff_added_bg = tint(bg, ansi_green, diff_alpha)
    diff_removed_bg = tint(bg, ansi_red, diff_alpha)

    # Syntax colors from ANSI palette
    ansi_yellow = ansi_to_color(tc.get("yellow", 3))

    return Theme(
        name="system",
        mode=detected_mode,
        primary=ansi_yellow,
        secondary=ansi_yellow,
        error=ansi_red,
        warning=ansi_yellow,
        success=fg,
        info=ansi_yellow,
        text=fg,
        text_muted=text_muted,
        background=Color(bg.r, bg.g, bg.b, 0.0),
        background_panel=bg,
        background_element=tint(bg, grays[2] if len(grays) > 2 else bg, 0.5),
        background_menu=tint(bg, Color(0, 0, 0) if is_dark else Color(255, 255, 255), 0.3),
        border=grays[3] if len(grays) > 3 else text_muted,
        border_active=ansi_yellow,
        border_subtle=grays[1] if len(grays) > 1 else text_muted,
        selected_list_item_text=ansi_yellow,
        diff_added=tint(bg, ansi_green, 0.7),
        diff_removed=tint(bg, ansi_red, 0.7),
        diff_context=text_muted,
        diff_added_bg=diff_added_bg,
        diff_removed_bg=diff_removed_bg,
        markdown_heading=ansi_yellow,
        markdown_code=fg,
        markdown_link=ansi_yellow,
        syntax_comment=text_muted,
        syntax_keyword=ansi_yellow,
        syntax_function=ansi_yellow,
        syntax_string=fg,
        syntax_number=ansi_yellow,
        syntax_type=ansi_yellow,
        thinking_opacity=0.5 if is_dark else 0.6,
    )


def get_terminal_colors() -> dict[str, Any]:
    """Attempt to read the terminal's ANSI color palette.

    On most terminals we can't directly query the palette, so we return
    sensible defaults. If the terminal supports OSC queries (like kitty,
    iTerm2), those could be used here.
    """
    return {
        "default_background": "#1a1b26",
        "default_foreground": "#c0caf5",
        "black": 0,
        "red": 1,
        "green": 2,
        "yellow": 3,
        "blue": 4,
        "magenta": 5,
        "cyan": 6,
        "white": 7,
    }
