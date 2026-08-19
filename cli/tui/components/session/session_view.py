"""
SessionView — renders a conversation as a scroll of messages.

Supports sticky scroll, scroll acceleration, message navigation
(next/prev/first/last), code concealment, and revert indicators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text as RichText

from ..primitives import ScrollBox
from ...theming.theme import Theme
from .message import Message, UserMessage, AssistantMessage
from .parts import MessagePart


@dataclass
class RevertMarker:
    """A visual marker for reverted messages."""

    count: int = 0
    diff_files: list[dict] = field(default_factory=list)
    shortcut: str = "/undo"


class SessionView:
    """
    Renders a conversation as a scroll of messages.

    - Sticky scroll keeps message headers visible
    - Scroll acceleration speeds up repeated input
    - Message navigation finds next/prev visible message
    - Revert markers show when messages were undone
    """

    def __init__(
        self,
        theme: Theme | None = None,
        thinking_mode: str = "collapsed",
        show_timestamps: bool = True,
        conceal_code: bool = True,
        sticky_scroll: bool = True,
        scroll_acceleration: float = 1.0,
    ) -> None:
        self.theme = theme
        self.thinking_mode = thinking_mode
        self.show_timestamps = show_timestamps
        self.conceal_code = conceal_code
        self.sticky_scroll = sticky_scroll
        self.scroll_acceleration = scroll_acceleration
        self.scrollbox = ScrollBox(
            max_lines=500,
            sticky_scroll=sticky_scroll,
            scroll_acceleration=scroll_acceleration,
            theme=theme,
        )
        self._messages: list[Message] = []
        self._revert_markers: dict[int, RevertMarker] = {}

    @property
    def messages(self) -> list[Message]:
        return self._messages

    def add_message(self, message: Message) -> None:
        """Add a message to the conversation."""
        self._messages.append(message)
        self._render_to_scrollbox()

    def add_messages(self, messages: list[Message]) -> None:
        self._messages.extend(messages)
        self._render_to_scrollbox()

    def _render_to_scrollbox(self) -> None:
        """Re-render all messages into the scrollbox."""
        self.scrollbox._lines = []
        for i, msg in enumerate(self._messages):
            if msg.reverted:
                marker = self._revert_markers.get(i)
                if marker:
                    self.scrollbox.add_lines(self._render_revert(marker).splitlines())
                continue
            renderable = self._render_message(msg)
            self.scrollbox.add_lines(self._renderable_to_lines(renderable))

    def _render_message(self, msg: Message) -> RenderableType:
        if msg.role == "user":
            renderer = UserMessage(msg, self.theme)
        else:
            renderer = AssistantMessage(msg, self.theme, self.thinking_mode)
        return renderer.render()

    def _render_revert(self, marker: RevertMarker) -> str:
        lines = [f"  [dim]{marker.count} message(s) reverted[/]"]
        lines.append(f"  [dim]{marker.shortcut} or /redo to restore[/]")
        for f in marker.diff_files:
            adds = f.get("additions", 0)
            dels = f.get("deletions", 0)
            parts = [f["filename"]]
            if adds > 0:
                parts.append(f" [green]+{adds}[/]")
            if dels > 0:
                parts.append(f" [red]-{dels}[/]")
            lines.append("  " + "".join(parts))
        return "\n".join(lines)

    def _renderable_to_lines(self, renderable: RenderableType) -> list[str]:
        """Convert a Rich renderable to plain text lines."""
        from rich.console import Console
        import io
        console = Console(file=io.StringIO(), width=80, force_terminal=False)
        console.print(renderable)
        return console.file.getvalue().splitlines()

    def scroll_to_message(self, message_id: str) -> None:
        """Scroll to a specific message by ID."""
        for i, msg in enumerate(self._messages):
            if msg.id == message_id:
                # Find the line offset in the scrollbox
                offset = 0
                for j in range(i):
                    offset += len(self._renderable_to_lines(self._render_message(self._messages[j])))
                self.scrollbox._offset = offset
                return

    def find_next_message(self, direction: str = "next") -> str | None:
        """Find the next/previous visible message ID."""
        visible = [m for m in self._messages if not m.reverted]
        if not visible:
            return None
        if direction == "next":
            return visible[0].id
        else:
            return visible[-1].id

    def scroll_top(self) -> None:
        self.scrollbox.scroll_to_top()

    def scroll_bottom(self) -> None:
        self.scrollbox.scroll_to_bottom()

    def toggle_code_concealment(self) -> None:
        """Toggle code block concealment."""
        self.conceal_code = not self.conceal_code
        self._render_to_scrollbox()

    def cycle_thinking_mode(self) -> str:
        """Cycle through thinking display modes."""
        modes = ("hide", "collapsed", "expanded")
        idx = modes.index(self.thinking_mode)
        self.thinking_mode = modes[(idx + 1) % len(modes)]
        self._render_to_scrollbox()
        return self.thinking_mode

    def render(self) -> RenderableType:
        return self.scrollbox.render()
