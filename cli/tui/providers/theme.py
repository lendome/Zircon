"""
ThemeProvider — manages the active theme, mode detection, and theme switching.

Wraps the theming/ system and exposes it via the context registry.
Components read `theme()` to get the current resolved Theme.

Reactive: when the mode changes (dark/light toggle) or the theme name
changes, the signal updates and all dependent components re-render.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..context import Context, ContextRegistry
from ..reactive.signal import Signal, signal, computed, persistent_signal
from ..theming.theme import Theme, ThemeMode
from ..theming.themes import get_theme, list_themes
from ..theming.detection import ThemeModeDetector
from .base import Provider


class ThemeProvider(Provider):
    name = "theme"

    def __init__(self, theme_name: str = "tokyo-night") -> None:
        self._theme_name = theme_name

    def provide(self, registry: ContextRegistry) -> Any:
        # Reactive mode detection (can be locked)
        detector = ThemeModeDetector()

        # Persisted theme name — survives across restarts
        theme_name_signal = persistent_signal(
            "theme_name", self._theme_name,
        )

        # Resolved theme is computed from name + mode
        theme_signal: Signal[Theme] = computed(
            lambda: get_theme(theme_name_signal.get(), detector.signal.get())
        )

        ctx = Context(name=self.name)
        ctx.set(theme_signal)
        registry.register(ctx)

        # Also register the detector and name signal for theme switching
        detector_ctx = Context(name="theme_detector")
        detector_ctx.set(detector)
        registry.register(detector_ctx)

        name_ctx = Context(name="theme_name")
        name_ctx.set(theme_name_signal)
        registry.register(name_ctx)

        return theme_signal
