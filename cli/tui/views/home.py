"""
Home route layout — centers the logo and prompt vertically.

  Box(flex_grow=1, align=center):
    Spacer()
    Slot(name="home_logo", mode="replace"): Logo()
    Spacer()
    Box(width=100%, max_width=prompt_max_width):
      Slot(name="home_prompt", mode="replace", ref=bind): Prompt()
    Slot(name="home_bottom")
    Spacer()
    Toast()
  Slot(name="home_footer", mode="single_winner")
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.text import Text as RichText

from ..theming.theme import Theme


class HomeLayout:
    """Renders the home route with centered logo and prompt."""

    def __init__(
        self,
        logo: RenderableType | None = None,
        prompt: RenderableType | None = None,
        footer: RenderableType | None = None,
        theme: Theme | None = None,
        max_width: int | str = "auto",
        terminal_width: int = 80,
    ) -> None:
        self.logo = logo
        self.prompt = prompt
        self.footer = footer
        self.theme = theme
        self._max_width = max_width
        self._terminal_width = terminal_width

    @property
    def prompt_max_width(self) -> int:
        if self._max_width == "auto":
            return max(75, int(self._terminal_width * 0.7))
        if isinstance(self._max_width, int):
            return self._max_width
        return 75

    def render(self) -> RenderableType:
        parts: list[RenderableType] = []
        parts.append(RichText(""))

        if self.logo is not None:
            parts.append(Align.center(self.logo))
            parts.append(RichText(""))

        if self.prompt is not None:
            parts.append(Align.center(self.prompt))

        if self.footer is not None:
            parts.append(self.footer)

        return Group(*parts)
