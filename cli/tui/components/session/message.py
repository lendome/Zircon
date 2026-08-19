"""
Message rendering — user and assistant messages with part-based content.

User messages get a colored left border matching the agent.
Assistant messages show metadata (model, duration, error) in a footer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text as RichText

from ...theming.theme import Theme
from .parts import MessagePart, render_part


@dataclass
class Message:
    """A single message in a conversation."""

    id: str = ""
    role: str = "user"  # "user" | "assistant" | "system"
    agent: str = ""
    parts: list[MessagePart] = field(default_factory=list)
    timestamp: float = 0.0
    model: str = ""
    duration: float = 0.0
    error: str = ""
    queued: bool = False
    reverted: bool = False
    has_subagent: bool = False
    has_running_subagents: bool = False


class UserMessage:
    """Renders a user message with agent-colored left border."""

    def __init__(self, message: Message, theme: Theme | None = None, agent_color: str = "cyan") -> None:
        self.message = message
        self.theme = theme
        self.agent_color = agent_color

    def render(self) -> RenderableType:
        border_color = "grey"
        if self.theme is not None:
            border_color = self.theme.border.to_rich()

        content_parts: list[RenderableType] = []

        for i, part in enumerate(self.message.parts):
            is_last = i == len(self.message.parts) - 1
            content_parts.append(render_part(part, self.theme, is_last))

        # File attachment chips
        file_parts = [p for p in self.message.parts if p.type == "file"]
        if file_parts:
            chips: list[str] = []
            for fp in file_parts:
                chips.append(f"[{self.agent_color}] File [/] [dim]{fp.filename}[/]")
            content_parts.append(RichText.from_markup("  ".join(chips)))

        # Queued indicator or timestamp
        if self.message.queued:
            content_parts.append(RichText.from_markup(f"[{self.agent_color}] QUEUED [/]"))
        elif self.message.timestamp:
            t = time.strftime("%H:%M", time.localtime(self.message.timestamp))
            content_parts.append(RichText(f"  [dim]{t}[/]"))

        return Panel(
            Group(*content_parts) if content_parts else RichText(""),
            border_style=border_color,
            padding=(0, 0),
        )


class AssistantMessage:
    """Renders an assistant message with parts and metadata footer."""

    def __init__(
        self,
        message: Message,
        theme: Theme | None = None,
        thinking_mode: str = "collapsed",
    ) -> None:
        self.message = message
        self.theme = theme
        self.thinking_mode = thinking_mode

    def render(self) -> RenderableType:
        content_parts: list[RenderableType] = []

        for i, part in enumerate(self.message.parts):
            is_last = i == len(self.message.parts) - 1
            content_parts.append(render_part(part, self.theme, is_last, self.thinking_mode))

        # Metadata footer
        footer_parts: list[str] = []
        if self.message.has_subagent:
            footer_parts.append("[dim]view subagents[/]")
            if self.message.has_running_subagents:
                footer_parts.append("[dim]· background[/]")

        if self.message.error and not self.message.reverted:
            err_style = "bold red"
            if self.theme is not None:
                err_style = f"bold {self.theme.error.to_rich()}"
            content_parts.append(
                Panel(
                    RichText(self.message.error, style=f"dim"),
                    border_style=self.theme.error.to_rich() if self.theme else "red",
                    padding=(0, 0),
                )
            )

        if footer_parts:
            content_parts.append(RichText.from_markup("  " + "  ".join(footer_parts)))

        return Group(*content_parts) if content_parts else RichText("")
