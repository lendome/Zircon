"""
Renderer — terminal rendering with configurable FPS, mouse, and key handling.

The renderer is an acquired resource — it must be cleaned up on exit.
Use the Renderer as a context manager or call destroy() explicitly.
"""

from __future__ import annotations

from .renderer import Renderer, RendererConfig
from .dimensions import use_terminal_dimensions, TerminalDimensions

__all__ = ["Renderer", "RendererConfig", "use_terminal_dimensions", "TerminalDimensions"]
