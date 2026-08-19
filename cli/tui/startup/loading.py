"""
Startup loading screen — shown while plugins and sync initialize.

Skippable via fast-boot env var (OPENCODE_FAST_BOOT).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from rich.align import Align
from rich.console import RenderableType
from rich.text import Text as RichText


@dataclass
class StartupConfig:
    skip_initial_loading: bool = False
    initial_route: str | None = None

    @staticmethod
    def from_env() -> "StartupConfig":
        return StartupConfig(
            skip_initial_loading=bool(os.environ.get("OPENCODE_FAST_BOOT")),
            initial_route=os.environ.get("OPENCODE_ROUTE"),
        )


class StartupLoading:
    """Renders a loading screen while the app initializes."""

    def __init__(self, ready_signal: Any | None = None) -> None:
        self._ready = ready_signal

    def render(self) -> RenderableType:
        return Align.center(RichText("Loading...", style="dim cyan"))
