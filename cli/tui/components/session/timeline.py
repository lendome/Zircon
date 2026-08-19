"""
Timeline dialog — shows all messages in a compact list for jumping.

Usage:
    DialogTimeline(
        on_move=lambda msg_id: session_view.scroll_to_message(msg_id),
        messages=session_view.messages,
    )
"""

from __future__ import annotations

from typing import Any, Callable

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text as RichText

from ...dialogs.dialog_select import DialogSelect, DialogOption
from ...theming.theme import Theme
from .message import Message


class TimelineDialog:
    """
    A dialog showing all messages in a compact timeline.

    Selecting a message scrolls the session view to that point.
    """

    def __init__(
        self,
        messages: list[Message],
        on_move: Callable[[str], None] | None = None,
        theme: Theme | None = None,
    ) -> None:
        self.messages = messages
        self.on_move = on_move
        self.theme = theme
        self._dialog: DialogSelect | None = None

    def build(self) -> DialogSelect:
        """Build the DialogSelect for the timeline."""
        options: list[DialogOption] = []
        for msg in self.messages:
            if msg.reverted:
                continue
            # Extract first text part for preview
            text_parts = [p for p in msg.parts if p.type == "text"]
            preview = text_parts[0].text[:60] if text_parts else f"[{msg.role}]"
            preview = preview.replace("\n", " ").strip()
            if not preview:
                preview = f"[{msg.role}]"

            role_label = "You" if msg.role == "user" else "AI"
            options.append(DialogOption(
                title=f"[{role_label}] {preview}",
                value=msg.id,
                category=msg.role,
                on_select=lambda opt, oid=msg.id: self._on_select(oid),
            ))

        self._dialog = DialogSelect(
            title="Timeline",
            options=options,
            on_move=self.on_move,
            theme=self.theme,
            size="large",
        )
        return self._dialog

    def _on_select(self, message_id: str) -> None:
        if self.on_move is not None:
            self.on_move(message_id)

    def render(self) -> RenderableType:
        if self._dialog is None:
            self.build()
        return self._dialog.render() if self._dialog else RichText("")
