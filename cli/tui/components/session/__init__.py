"""
Session & message rendering.

Renders conversations as a scroll of messages where each message is
composed of typed parts (text, reasoning, tool calls, file diffs).
Supports code concealment, thinking blocks with dimmed syntax, diff
rendering, message navigation, inline permission prompts, timeline
jumping, and export.
"""

from __future__ import annotations

from .parts import PartRenderer, PART_MAPPING, MessagePart
from .message import UserMessage, AssistantMessage, Message
from .session_view import SessionView
from .timeline import TimelineDialog
from .export import export_session

__all__ = [
    "PartRenderer",
    "PART_MAPPING",
    "MessagePart",
    "UserMessage",
    "AssistantMessage",
    "Message",
    "SessionView",
    "TimelineDialog",
    "export_session",
]
