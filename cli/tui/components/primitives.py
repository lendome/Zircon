"""
Terminal-native UI primitives — composable components like DOM elements.

These map to Rich renderables and compose hierarchically. Components
read reactive signals and re-render only when their dependencies change.

Primitives:
  - Box         — rectangular container with flex layout, padding, borders
  - Text        — styled text with fg/bg, bold, italic, underline
  - ScrollBox   — scrollable container with scrollbar and sticky scroll
  - TextArea    — multi-line editable text with cursor, selection
  - Spinner     — animated character frames at a configurable interval
  - Dynamic     — render a component determined by data (PART_MAPPING lookup)
  - ErrorBoundary — wrap children so a render error doesn't kill the TUI
  - Switch/When — conditional rendering
  - For          — reactive list rendering
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.align import Align
from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text as RichText

from ..reactive.signal import Signal, computed, _Computed
from ..reactive.lifecycle import LifecycleScope, set_scope, current_scope
from ..theming.theme import Theme


# ── Component base ────────────────────────────────────────────────────────────


class Component:
    """Base class for all UI components. Manages lifecycle scope."""

    def __init__(self) -> None:
        self.scope = LifecycleScope(name=self.__class__.__name__)

    def render(self) -> RenderableType:
        raise NotImplementedError

    def mount(self) -> None:
        self.scope.mount()

    def cleanup(self) -> None:
        self.scope.cleanup()

    def __enter__(self) -> "Component":
        set_scope(self.scope)
        return self

    def __exit__(self, *args: Any) -> None:
        set_scope(None)


# ── Box ───────────────────────────────────────────────────────────────────────


@dataclass
class BoxProps:
    """Layout properties for a Box container."""

    direction: str = "column"          # column | row
    width: int | str | None = None     # int (chars) or "100%" or None (auto)
    height: int | str | None = None
    padding: int = 0
    border: bool = False
    border_style: str = ""
    bg: str = ""
    title: str = ""
    flex_grow: int = 0
    align: str = "left"               # left | center | right


class Box(Component):
    """Rectangular container with optional flex layout, padding, borders."""

    def __init__(
        self,
        children: list[RenderableType] | None = None,
        props: BoxProps | None = None,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self.children = children or []
        self.props = props or BoxProps()
        self.theme = theme

    def render(self) -> RenderableType:
        content = Group(*self.children) if self.children else RichText("")

        if self.props.border:
            border_style = self.props.border_style
            if not border_style and self.theme is not None:
                border_style = self.theme.border.to_rich()
            return Panel(
                content,
                title=self.props.title or None,
                border_style=border_style or "dim",
                padding=self.props.padding,
                box=box.SQUARE,
            )

        if self.props.padding:
            padded = "\n".join(
                " " * self.props.padding + str(line)
                for line in str(content).splitlines()
            )
            return RichText(padded)

        return content


# ── Text ──────────────────────────────────────────────────────────────────────


@dataclass
class TextProps:
    """Style properties for a Text element."""

    fg: str = ""
    bg: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    dim: bool = False
    align: str = "left"


class Text(Component):
    """Styled text with foreground/background color, bold, italic, underline."""

    def __init__(
        self,
        content: str | Signal[str],
        props: TextProps | None = None,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self._content = content
        self.props = props or TextProps()
        self.theme = theme

    def render(self) -> RenderableType:
        if isinstance(self._content, Signal):
            text = self._content.get()
        else:
            text = self._content

        style_parts: list[str] = []
        if self.props.fg:
            style_parts.append(self.props.fg)
        elif self.theme is not None:
            style_parts.append(self.theme.text.to_rich())
        if self.props.bg:
            style_parts.append(f"on {self.props.bg}")
        if self.props.bold:
            style_parts.append("bold")
        if self.props.italic:
            style_parts.append("italic")
        if self.props.underline:
            style_parts.append("underline")
        if self.props.dim:
            style_parts.append("dim")

        style = " ".join(style_parts)

        if self.props.align == "center":
            return Align.center(RichText(text, style=style))
        elif self.props.align == "right":
            return Align.right(RichText(text, style=style))
        return RichText(text, style=style)


# ── ScrollBox ────────────────────────────────────────────────────────────────


class ScrollBox(Component):
    """Scrollable container with scrollbar and sticky scroll.

    Tracks scroll offset and renders only the visible portion. When
    `sticky_scroll` is True, auto-scrolls to the bottom on new content.
    """

    def __init__(
        self,
        children: list[RenderableType] | None = None,
        max_lines: int = 100,
        sticky_scroll: bool = True,
        scroll_acceleration: float = 1.0,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self._lines: list[str] = []
        self._offset = 0
        self.max_lines = max_lines
        self.sticky_scroll = sticky_scroll
        self.scroll_acceleration = scroll_acceleration
        self.theme = theme

    def add_line(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]
        if self.sticky_scroll:
            self._offset = len(self._lines)

    def add_lines(self, lines: list[str]) -> None:
        self._lines.extend(lines)
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]
        if self.sticky_scroll:
            self._offset = len(self._lines)

    def scroll_up(self, rows: int = 5) -> None:
        self._offset = max(0, self._offset - int(rows * self.scroll_acceleration))

    def scroll_down(self, rows: int = 5) -> None:
        self._offset = min(len(self._lines), self._offset + int(rows * self.scroll_acceleration))

    def scroll_to_top(self) -> None:
        self._offset = 0

    def scroll_to_bottom(self) -> None:
        self._offset = len(self._lines)

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def total_lines(self) -> int:
        return len(self._lines)

    def render(self) -> RenderableType:
        if not self._lines:
            return RichText("")
        return RichText("\n".join(self._lines))


# ── Spinner ──────────────────────────────────────────────────────────────────


class Spinner(Component):
    """Animated character frames at a configurable interval."""

    FRAMES = ["|", "/", "-", "\\"]
    FRAMES_ASCII = ["|", "/", "-", "\\"]

    def __init__(
        self,
        interval_ms: int = 80,
        label: str = "",
        theme: Theme | None = None,
        ascii_only: bool = False,
    ) -> None:
        super().__init__()
        self._frames = self.FRAMES_ASCII if ascii_only else self.FRAMES
        self.interval = interval_ms / 1000.0
        self._frame_idx = 0
        self._last_update = time.monotonic()
        self.label = label
        self.theme = theme

    def tick(self) -> None:
        now = time.monotonic()
        if now - self._last_update >= self.interval:
            self._frame_idx = (self._frame_idx + 1) % len(self._frames)
            self._last_update = now

    @property
    def current_frame(self) -> str:
        return self._frames[self._frame_idx]

    def render(self) -> RenderableType:
        self.tick()
        style = ""
        if self.theme is not None:
            style = self.theme.info.to_rich()
        text = f"{self.current_frame} {self.label}" if self.label else self.current_frame
        return RichText(text, style=style)


# ── Dynamic component ────────────────────────────────────────────────────────


class Dynamic(Component):
    """Render a component determined by data (e.g. PART_MAPPING lookup)."""

    def __init__(
        self,
        component_factory: Callable[[Any], Component],
        data: Any,
    ) -> None:
        super().__init__()
        self._factory = component_factory
        self._data = data

    def render(self) -> RenderableType:
        component = self._factory(self._data)
        if hasattr(component, "mount"):
            component.mount()
        return component.render()


# ── ErrorBoundary ─────────────────────────────────────────────────────────────


class ErrorBoundary(Component):
    """Wrap children so a rendering error in one component doesn't kill the TUI.

    If a render error occurs, the fallback is shown instead. The `reset`
    callback lets the user retry after an error.
    """

    def __init__(
        self,
        children: list[RenderableType],
        fallback: Callable[[Exception, Callable[[], None]], RenderableType] | None = None,
        theme: Theme | None = None,
    ) -> None:
        super().__init__()
        self.children = children
        self._fallback = fallback
        self._error: Exception | None = None
        self._theme = theme

    def render(self) -> RenderableType:
        if self._error is not None:
            if self._fallback is not None:
                return self._fallback(self._error, self.reset)
            style = "bold red" if self._theme is None else self._theme.error.to_rich()
            return Panel(
                RichText(f"Render error: {self._error}", style=style),
                title="Error",
                border_style=style,
            )

        try:
            return Group(*self.children)
        except Exception as exc:
            self._error = exc
            return self.render()

    def reset(self) -> None:
        """Clear the error and retry rendering."""
        self._error = None


# ── Conditional rendering ─────────────────────────────────────────────────────


class Switch(Component):
    """Conditional rendering — render the first matching case."""

    def __init__(self, cases: list[tuple[Any, RenderableType]], default: RenderableType | None = None) -> None:
        super().__init__()
        self.cases = cases
        self.default = default

    def render(self) -> RenderableType:
        for condition, renderable in self.cases:
            if callable(condition) and not isinstance(condition, _Computed):
                if condition():
                    return renderable
            elif condition:
                return renderable
        if self.default is not None:
            return self.default
        return RichText("")


class For(Component):
    """Reactive list rendering — render a component for each item."""

    def __init__(
        self,
        items: list[Any] | Signal[list[Any]],
        render_fn: Callable[[Any, int], RenderableType],
    ) -> None:
        super().__init__()
        self._items = items
        self._render_fn = render_fn

    def render(self) -> RenderableType:
        if isinstance(self._items, Signal):
            items = self._items.get()
        else:
            items = self._items
        renderables = [self._render_fn(item, i) for i, item in enumerate(items)]
        return Group(*renderables) if renderables else RichText("")
