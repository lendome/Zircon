"""
Path normalization — handle platform-specific path separators.

Mention paths always use / (even on Windows) for consistency.
"""

from __future__ import annotations

import os
from pathlib import Path


def normalize_mention_path(file_path: str, base_dir: str, platform: str = "") -> str:
    """Normalize a file path for use in @mentions.

    - Relative paths are preferred (shorter, more readable)
    - Always uses / as separator (even on Windows)
    """
    try:
        absolute = str(Path(file_path).resolve())
        relative = os.path.relpath(absolute, str(Path(base_dir).resolve()))
        if relative and not relative.startswith("..") and not os.path.isabs(relative):
            return relative.replace(os.sep, "/")
        return absolute.replace(os.sep, "/")
    except (ValueError, OSError):
        return file_path.replace(os.sep, "/")


def pasted_filepath(value: str, platform: str = "") -> str:
    """Detect and normalize a pasted file path.

    - Strips surrounding quotes
    - Handles file:// URLs
    - Windows paths don't need backslash unescaping
    - Unix paths unescape backslashes
    """
    raw = value.strip().strip('"').strip("'")

    if raw.startswith("file://"):
        return _file_url_to_path(raw)

    if platform == "win32" or (not platform and os.name == "nt"):
        return raw

    # Unix: unescape backslashes
    return raw.replace("\\\\", "\\")


def _file_url_to_path(url: str) -> str:
    """Convert a file:// URL to a local path."""
    if url.startswith("file:///"):
        # Unix absolute path
        return url[7:]
    elif url.startswith("file://"):
        # Windows UNC path or local path
        return url[7:].replace("/", "\\")
    return url
