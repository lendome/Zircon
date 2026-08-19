"""
Theme mode detection — detect dark/light from the terminal background.

The TUI detects the mode automatically and can be locked so it doesn't
follow terminal changes.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from ..reactive.signal import Signal, signal
from .colors import Color, parse_hex, luminance
from .theme import ThemeMode


def _detect_dark_mode() -> bool:
    """Heuristic dark/light detection from environment and terminal."""
    # Check COLORFGBG (common in xterm/urxvt) — format: "fg;bg"
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg and ";" in colorfgbg:
        parts = colorfgbg.split(";")
        if len(parts) >= 2:
            bg_index = int(parts[1])
            return bg_index < 7  # ANSI 0-6 are dark backgrounds

    # Check common dark mode env vars
    dark_term = os.environ.get("COLOR_TERM", "").lower()
    if "dark" in dark_term:
        return True
    if "light" in dark_term:
        return False

    # Check terminal background color via OSC query (best effort)
    # Most terminals don't respond to this synchronously, so we default to dark
    colorterm = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()

    # Default assumption: most developer terminals are dark
    return True


def detect_terminal_mode() -> ThemeMode:
    """Detect whether the terminal is in dark or light mode."""
    return ThemeMode.DARK if _detect_dark_mode() else ThemeMode.LIGHT


class ThemeModeDetector:
    """Detects and optionally locks the terminal's dark/light mode."""

    def __init__(self) -> None:
        self.mode: ThemeMode = detect_terminal_mode()
        self.locked: bool = False
        self._signal: Signal[ThemeMode] = signal(self.mode)

    @property
    def signal(self) -> Signal[ThemeMode]:
        return self._signal

    def lock(self) -> None:
        self.locked = True

    def unlock(self) -> None:
        self.locked = False
        new_mode = detect_terminal_mode()
        if new_mode != self.mode:
            self.mode = new_mode
            self._signal.set(new_mode)

    def toggle(self) -> None:
        self.lock()
        new_mode = ThemeMode.LIGHT if self.mode == ThemeMode.DARK else ThemeMode.DARK
        self.mode = new_mode
        self._signal.set(new_mode)

    def refresh(self) -> None:
        if not self.locked:
            new_mode = detect_terminal_mode()
            if new_mode != self.mode:
                self.mode = new_mode
                self._signal.set(new_mode)
