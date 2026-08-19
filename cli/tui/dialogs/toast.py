"""
Toast notifications — transient non-blocking messages.

  - Auto-dismiss after configurable duration (default 5000ms)
  - Render as a single right-aligned line colored by variant
  - Only show one at a time (new toast replaces old)

Variants: info, success, warning, error

Usage:
    toast.show("Copied to clipboard", variant="info")
    toast.error(some_error)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from rich.align import Align
from rich.console import RenderableType
from rich.text import Text as RichText

from ..theming.theme import Theme


class ToastVariant(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Toast:
    """A single toast notification."""

    message: str = ""
    title: str = ""
    variant: ToastVariant = ToastVariant.INFO
    duration: float = 5.0
    created_at: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) >= self.duration


class ToastManager:
    """
    Manages toast notifications.

    Only one toast is shown at a time — new toasts replace old ones.
    Auto-dismisses after the configured duration.
    """

    def __init__(self, theme: Theme | None = None) -> None:
        self.theme = theme
        self._current: Toast | None = None

    @property
    def current(self) -> Toast | None:
        return self._current

    @property
    def is_visible(self) -> bool:
        if self._current is None:
            return False
        if self._current.is_expired:
            self._current = None
            return False
        return True

    def show(
        self,
        message: str,
        title: str = "",
        variant: ToastVariant | str = ToastVariant.INFO,
        duration: float = 5.0,
    ) -> None:
        """Show a toast notification."""
        if isinstance(variant, str):
            variant = ToastVariant(variant)
        self._current = Toast(
            message=message,
            title=title,
            variant=variant,
            duration=duration,
        )

    def info(self, message: str, duration: float = 5.0) -> None:
        self.show(message, variant=ToastVariant.INFO, duration=duration)

    def success(self, message: str, duration: float = 3.0) -> None:
        self.show(message, variant=ToastVariant.SUCCESS, duration=duration)

    def warning(self, message: str, duration: float = 3.0) -> None:
        self.show(message, variant=ToastVariant.WARNING, duration=duration)

    def error(self, message: str, duration: float = 5.0) -> None:
        self.show(message, variant=ToastVariant.ERROR, duration=duration)

    def dismiss(self) -> None:
        self._current = None

    def _variant_style(self, variant: ToastVariant) -> str:
        if self.theme is None:
            return {
                ToastVariant.INFO: "cyan",
                ToastVariant.SUCCESS: "green",
                ToastVariant.WARNING: "yellow",
                ToastVariant.ERROR: "red",
            }.get(variant, "dim")
        return {
            ToastVariant.INFO: self.theme.info.to_rich(),
            ToastVariant.SUCCESS: self.theme.success.to_rich(),
            ToastVariant.WARNING: self.theme.warning.to_rich(),
            ToastVariant.ERROR: self.theme.error.to_rich(),
        }.get(variant, "dim")

    def render(self) -> RenderableType:
        """Render the current toast (or empty if none)."""
        if not self.is_visible or self._current is None:
            return RichText("")

        toast = self._current
        style = self._variant_style(toast.variant)

        line = RichText()
        if toast.title:
            line.append(f"{toast.title} ", style=f"bold {style}")
        line.append(toast.message, style=style)
        return Align.right(line)
