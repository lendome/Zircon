"""
Theme prewarming — fetch terminal palette before rendering to avoid
first-paint flash when the "system" theme is used.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def prewarm_theme(renderer: Any | None = None) -> str:
    """Prewarm the theme by detecting terminal mode.

    Returns "dark" or "light". Doesn't block if renderer is unavailable.
    """
    from ..theming.detection import detect_terminal_mode
    try:
        mode = detect_terminal_mode()
        return mode.value
    except Exception:
        return "dark"
