"""
Error handling - session errors, aborted messages, extraction.
"""

from __future__ import annotations

from typing import Any


def extract_error_message(error: Any) -> str:
    """Extract a human-readable error message from various error formats."""
    if error is None:
        return "Unknown error"
    if isinstance(error, dict):
        if error.get("data", {}).get("message"):
            return str(error["data"]["message"])
        if error.get("message"):
            return str(error["message"])
    if isinstance(error, Exception):
        return str(error)
    return str(error)


def handle_session_error(error: Any) -> str | None:
    """Handle a session error. Returns a toast message or None if ignored."""
    if error is None:
        return None
    name = ""
    if isinstance(error, dict):
        name = error.get("name", "")
    elif isinstance(error, Exception):
        name = type(error).__name__
    if name == "MessageAbortedError":
        return None
    return extract_error_message(error)
