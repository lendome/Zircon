"""
OS notifications — terminal-native notification triggers and text normalization.

Uses the terminal's notification capabilities (OSC sequences, terminal bell,
or platform notifications). All text is sanitized before sending.
"""

from __future__ import annotations

import re
import sys
from typing import Any


MESSAGE_LIMIT = 240
TITLE_LIMIT = 80


def normalize_text(input_text: str | None, fallback: str = "", limit: int = MESSAGE_LIMIT) -> str:
    """Sanitize text for OS notifications.

    - Strip ANSI escape codes (terminal colors would break notifications)
    - Collapse newlines to spaces
    - Strip control characters
    - Truncate to limit
    """
    if not input_text:
        return fallback

    # Strip ANSI escape codes
    text = re.sub(r"\x1b\[[0-9;]*m", "", input_text)
    # Collapse newlines and surrounding whitespace to single space
    text = re.sub(r"[ \t]*[\r\n]+[ \t]*", " ", text)
    # Strip control characters (except basic whitespace)
    text = re.sub(r"[\x00-\x09\x0B\x0C\x0E-\x1F\x7F-\x9F]", "", text)
    text = text.strip()

    if not text:
        return fallback

    return text[:limit]


def trigger_notification(renderer: Any, message: str, title: str = "CLI") -> bool:
    """Trigger an OS notification via the renderer.

    Uses the terminal's notification capabilities. Returns True on success.
    """
    normalized_message = normalize_text(message, "", MESSAGE_LIMIT)
    normalized_title = normalize_text(title, "CLI", TITLE_LIMIT)

    if not normalized_message:
        return False

    try:
        if hasattr(renderer, "trigger_notification"):
            return renderer.trigger_notification(normalized_message, normalized_title)
        # Fallback: terminal bell
        sys.stdout.write("\a")
        sys.stdout.flush()
        return True
    except Exception:
        return False
