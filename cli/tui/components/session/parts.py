"""
Part-based message rendering — each message is composed of typed parts.

Part types are extensible — new types can be added without changing the
message renderer. A PART_MAPPING dict maps type strings to renderers.

  PART_MAPPING = {
      "text":      TextPart,
      "reasoning": ReasoningPart,
      "tool":      ToolPart,
      "file":      FilePart,
  }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text as RichText

from ...theming.theme import Theme
from ...theming.syntax import generate_subtle_syntax


@dataclass
class MessagePart:
    """A single part of a message."""

    type: str  # "text", "reasoning", "tool", "file", "diff"
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    filename: str = ""
    diff: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class PartRenderer:
    """Base class for part renderers."""

    def __init__(self, theme: Theme | None = None) -> None:
        self.theme = theme

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        raise NotImplementedError


class TextPartRenderer(PartRenderer):
    """Renders text parts as Markdown."""

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        if not part.text.strip():
            return RichText("")
        return Markdown(part.text.strip())


class ReasoningPartRenderer(PartRenderer):
    """Renders reasoning/thinking blocks with readable, distinct styling."""

    THINKING_MODES = ("hide", "collapsed", "expanded")

    def __init__(self, theme: Theme | None = None, mode: str = "collapsed") -> None:
        super().__init__(theme)
        self.mode = mode

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        if self.mode == "hide":
            return RichText("")

        style = "dim white"
        border = "dim"
        if self.theme is not None:
            style = self.theme.text_muted.to_rich()
            border = self.theme.text_muted.to_rich()

        if self.mode == "collapsed":
            line_count = part.text.count("\n") + 1
            return Panel(
                RichText(f"Thinking... ({line_count} lines, press to expand)", style=style),
                title="Reasoning",
                border_style=border,
                box=box.SQUARE,
            )

        # Expanded — show full text with the normal readable text color.
        return Panel(
            RichText(part.text, style=style),
            title="Reasoning",
            border_style=border,
            box=box.SQUARE,
        )

    def cycle_mode(self) -> str:
        """Cycle through thinking modes: hide → collapsed → expanded → hide."""
        idx = self.THINKING_MODES.index(self.mode)
        self.mode = self.THINKING_MODES[(idx + 1) % len(self.THINKING_MODES)]
        return self.mode


class ToolPartRenderer(PartRenderer):
    """Renders tool call parts."""

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        style = "dim white"
        border = "dim"
        if self.theme is not None:
            style = self.theme.text_muted.to_rich()
            border = self.theme.text_muted.to_rich()

        args_preview = str(part.tool_args)[:120]
        if len(str(part.tool_args)) > 120:
            args_preview += "…"

        lines: list[RenderableType] = [
            Panel(
                RichText(f"{part.tool_name}({args_preview})", style=style),
                title="Tool Call",
                border_style=border,
                box=box.SQUARE,
            ),
        ]

        if part.tool_result:
            result = part.tool_result
            if "--- a/" in result or "+++ b/" in result or "diff --git" in result:
                lines.append(Panel(
                    Syntax(result, "diff", theme="ansi_dark", word_wrap=True),
                    title="Diff",
                    border_style=self.theme.warning.to_rich() if self.theme else "yellow",
                    box=box.SQUARE,
                ))
            else:
                preview = result[:300]
                if len(result) > 300:
                    preview += "…"
                lines.append(Panel(RichText(preview), title="Tool Result", border_style=border, box=box.SQUARE))

        return Group(*lines)


class FilePartRenderer(PartRenderer):
    """Renders file attachment parts."""

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        style = "cyan"
        if self.theme is not None:
            style = self.theme.info.to_rich()
        label = part.filename or "file"
        return RichText(f"  [{style}]File:[/] {label}", style="dim")


class DiffPartRenderer(PartRenderer):
    """Renders diff parts with theme diff colors."""

    def render(self, part: MessagePart, is_last: bool = False) -> RenderableType:
        border = "yellow"
        if self.theme is not None:
            border = self.theme.warning.to_rich()
        return Panel(
            Syntax(part.diff, "diff", theme="ansi_dark", word_wrap=True),
            title="Diff",
            border_style=border,
            box=box.SQUARE,
        )


PART_MAPPING: dict[str, type[PartRenderer]] = {
    "text": TextPartRenderer,
    "reasoning": ReasoningPartRenderer,
    "tool": ToolPartRenderer,
    "file": FilePartRenderer,
    "diff": DiffPartRenderer,
}


def render_part(
    part: MessagePart,
    theme: Theme | None = None,
    is_last: bool = False,
    thinking_mode: str = "collapsed",
) -> RenderableType:
    """Render a single message part using the PART_MAPPING."""
    renderer_cls = PART_MAPPING.get(part.type)
    if renderer_cls is None:
        return RichText(f"  [unknown part type: {part.type}]")

    kwargs: dict[str, Any] = {}
    if renderer_cls is ReasoningPartRenderer:
        kwargs["mode"] = thinking_mode
    renderer = renderer_cls(theme=theme, **kwargs)
    return renderer.render(part, is_last)
