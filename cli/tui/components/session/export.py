"""
Session export — full transcript export to markdown with options.

Options:
  - thinking: include reasoning/thinking blocks
  - tool_details: include tool call arguments and results
  - assistant_metadata: include model name, duration, error state
  - providers: provider info for assistant messages
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .message import Message


@dataclass
class ExportOptions:
    """Options for transcript export."""

    thinking: bool = False
    tool_details: bool = True
    assistant_metadata: bool = True
    providers: dict[str, str] | None = None
    filename: str = ""
    open_without_saving: bool = False


def export_session(
    session_id: str,
    messages: list[Message],
    options: ExportOptions | None = None,
) -> str:
    """
    Export a full conversation transcript to markdown.

    Args:
        session_id: Session identifier for the header
        messages: List of messages to export
        options: Export options (thinking, tool_details, etc.)

    Returns:
        Markdown-formatted transcript string
    """
    opts = options or ExportOptions()
    lines: list[str] = []

    # Header
    lines.append(f"# Session Export: {session_id}")
    lines.append(f"Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Messages: {len(messages)}")
    lines.append("")

    for msg in messages:
        if msg.reverted:
            lines.append(f"*-- {1} message reverted --*")
            lines.append("")
            continue

        role_label = "User" if msg.role == "user" else "Assistant"
        if msg.agent and msg.role == "user":
            role_label += f" (→ {msg.agent})"

        lines.append(f"## {role_label}")
        if msg.timestamp:
            lines.append(f"*{time.strftime('%H:%M:%S', time.localtime(msg.timestamp))}*")
        lines.append("")

        for part in msg.parts:
            if part.type == "text":
                lines.append(part.text)
                lines.append("")
            elif part.type == "reasoning":
                if opts.thinking:
                    lines.append("<details><summary>Reasoning</summary>")
                    lines.append("")
                    lines.append(part.text)
                    lines.append("")
                    lines.append("</details>")
                    lines.append("")
            elif part.type == "tool":
                if opts.tool_details:
                    lines.append(f"**Tool:** `{part.tool_name}`")
                    if part.tool_args:
                        lines.append(f"```json")
                        lines.append(str(part.tool_args))
                        lines.append("```")
                    if part.tool_result:
                        preview = part.tool_result[:500]
                        if len(part.tool_result) > 500:
                            preview += "\n... (truncated)"
                        lines.append(f"```")
                        lines.append(preview)
                        lines.append("```")
                    lines.append("")
            elif part.type == "file":
                lines.append(f"*File: {part.filename}*")
                lines.append("")
            elif part.type == "diff":
                lines.append("```diff")
                lines.append(part.diff)
                lines.append("```")
                lines.append("")

        # Assistant metadata footer
        if opts.assistant_metadata and msg.role == "assistant":
            meta_parts: list[str] = []
            if msg.model:
                meta_parts.append(f"Model: `{msg.model}`")
            if msg.duration > 0:
                meta_parts.append(f"Duration: {msg.duration:.1f}s")
            if msg.error:
                meta_parts.append(f"Error: {msg.error}")
            if opts.providers and msg.model:
                provider = opts.providers.get(msg.model, "")
                if provider:
                    meta_parts.append(f"Provider: {provider}")
            if meta_parts:
                lines.append(f"*{', '.join(meta_parts)}*")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
