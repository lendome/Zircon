"""
Prompt footer — a single status line below the prompt.

  [Status message]                  [mode] · [Model ID] · [Provider]

Status (plus an activity dot and interrupt hint while streaming) sits in
the left corner; permission mode, model, provider, variant, tokens, and
cost are right-aligned. An error, if any, gets its own line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text as RichText

from ..theming.theme import Theme


@dataclass
class FooterData:
    """Data for rendering the prompt footer."""

    agent_name: str = "Agent"
    agent_color: str = "cyan"
    model_id: str = ""
    provider: str = ""
    session_title: str = ""
    session_id: str = ""
    variant: str = ""
    permission_mode: str = "auto"  # auto | normal
    status_message: str = ""
    is_active: bool = False
    tokens_used: int = 0
    context_used_tokens: int = 0
    context_max_tokens: int = 0
    cost: float = 0.0
    show_interrupt: bool = False
    error_message: str = ""


class PromptFooter:
    """Renders the status footer below the prompt."""

    def __init__(self, theme: Theme | None = None) -> None:
        self.theme = theme
        self._data = FooterData()

    @property
    def data(self) -> FooterData:
        return self._data

    @data.setter
    def data(self, value: FooterData) -> None:
        self._data = value

    def update(self, **kwargs: Any) -> None:
        """Update footer data fields."""
        for k, v in kwargs.items():
            if hasattr(self._data, k):
                setattr(self._data, k, v)

    def render(self) -> RenderableType:
        """Render the footer: status left, mode/model/provider right."""
        d = self._data

        status_style = "dim"
        accent_style = d.agent_color
        if self.theme is not None:
            status_style = self.theme.text_muted.to_rich()
            accent_style = self.theme.primary.to_rich()

        # Left corner: activity dot + status + interrupt hint
        left_parts: list[str] = []
        if d.is_active:
            left_parts.append(f"[{accent_style}]*[/]")
        left_parts.append(f"[{status_style}]{d.status_message or 'Ready'}[/]")
        if d.show_interrupt:
            left_parts.append("[dim]Ctrl+C to interrupt · twice to exit[/]")

        # Right corner: mode · model · provider · variant · tokens · cost
        right_parts: list[str] = []
        if d.session_title:
            session_label = d.session_title
            if len(session_label) > 28:
                session_label = session_label[:25] + "..."
            right_parts.append(f"[{accent_style}]{session_label}[/]")
        elif d.session_id:
            right_parts.append(f"[{accent_style}]{d.session_id[:8]}[/]")
        if d.permission_mode:
            right_parts.append(f"[dim]{d.permission_mode}[/]")
        if d.model_id:
            right_parts.append(f"[bold]{d.model_id}[/]")
        if d.provider:
            right_parts.append(f"[dim]{d.provider}[/]")
        if d.variant:
            right_parts.append(f"[dim]{d.variant}[/]")
        if d.tokens_used > 0:
            right_parts.append(f"[dim]{d.tokens_used} tokens[/]")
        if d.context_max_tokens > 0:
            percent = min(100, round(d.context_used_tokens * 100 / d.context_max_tokens))
            right_parts.append(
                f"[dim]ctx {d.context_used_tokens:,}/{d.context_max_tokens:,} ({percent}%)[/]"
            )
        if d.cost > 0:
            right_parts.append(f"[dim]${d.cost:.4f}[/]")

        line = Table.grid(expand=True)
        line.add_column(justify="left", ratio=1, no_wrap=True, overflow="ellipsis")
        line.add_column(justify="right", no_wrap=True, overflow="ellipsis")
        line.add_row(
            RichText.from_markup(" ".join(left_parts)),
            RichText.from_markup(" · ".join(right_parts)),
        )

        if d.error_message:
            err_style = "bold red"
            if self.theme is not None:
                err_style = f"bold {self.theme.error.to_rich()}"
            return Group(line, RichText.from_markup(f"[{err_style}]Error: {d.error_message}[/]"))

        return line
