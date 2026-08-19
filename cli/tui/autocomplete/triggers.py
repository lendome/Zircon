"""
Trigger detection — detect @ and / autocomplete triggers.

Scans backward from the cursor for the trigger character with no
whitespace in between.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TriggerType(str, Enum):
    FILE = "@"
    SLASH = "/"
    NONE = ""


@dataclass
class LineRange:
    """A parsed line range from @file#42-50."""

    base_name: str = ""
    start_line: int | None = None
    end_line: int | None = None

    @property
    def has_range(self) -> bool:
        return self.start_line is not None

    @property
    def label(self) -> str:
        if not self.has_range:
            return ""
        if self.end_line and self.end_line > self.start_line:
            return f"#{self.start_line}-{self.end_line}"
        return f"#{self.start_line}"


def detect_trigger(text: str, cursor: int) -> tuple[TriggerType, int]:
    """Detect the autocomplete trigger at the cursor position.

    Returns (trigger_type, trigger_index). If no trigger, returns (NONE, -1).
    """
    if cursor == 0:
        return TriggerType.NONE, -1

    # "/" at position 0 with no space before cursor
    if text.startswith("/") and " " not in text[:cursor]:
        return TriggerType.SLASH, 0

    # "@" trigger — find nearest @ before cursor with no whitespace between
    idx = find_mention_trigger(text, cursor)
    if idx is not None:
        return TriggerType.FILE, idx

    return TriggerType.NONE, -1


def find_mention_trigger(text: str, cursor: int) -> int | None:
    """Find the nearest @ before cursor with no whitespace between."""
    for i in range(cursor - 1, -1, -1):
        ch = text[i]
        if ch == "@":
            between = text[i + 1:cursor]
            if " " not in between and "\n" not in between:
                return i
            return None
        if ch in (" ", "\n", "\t"):
            return None
    return None


def extract_line_range(input_text: str) -> tuple[str, LineRange]:
    """Parse @file#42-50 into (base_name, LineRange).

    Returns (original_input, empty_range) if no line range found.
    """
    hash_idx = input_text.rfind("#")
    if hash_idx == -1:
        return input_text, LineRange(base_name=input_text)

    base_name = input_text[:hash_idx]
    line_part = input_text[hash_idx + 1:]

    import re
    match = re.match(r"^(\d+)(?:-(\d*))?$", line_part)
    if not match:
        return input_text, LineRange(base_name=input_text)

    start_line = int(match.group(1))
    end_str = match.group(2)
    end_line = int(end_str) if end_str and start_line < int(end_str) else None

    return base_name, LineRange(
        base_name=base_name,
        start_line=start_line,
        end_line=end_line,
    )
