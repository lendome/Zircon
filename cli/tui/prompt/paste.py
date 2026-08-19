"""
Smart paste handling — large pastes are summarized as extmarks instead
of dumped inline. Detects file paths and reads them as attachments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PasteResult:
    """Result of processing a paste."""

    kind: str  # "text", "summary", "file", "binary", "image"
    text: str = ""
    display: str = ""
    filename: str = ""
    mime: str = ""
    content: bytes | str = b""


def smart_paste(text: str, repo_path: str | None = None) -> PasteResult:
    """
    Process pasted text intelligently.

    - File paths → read the file and return as attachment
    - Large pastes (3+ lines or 150+ chars) → summarize as extmark
    - Small pastes → insert directly
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    content = normalized.strip()
    line_count = content.count("\n") + 1 if content else 0

    # Detect pasted file paths
    if repo_path:
        file_result = detect_file_path(content, repo_path)
        if file_result is not None:
            return file_result

    # Summarize large pastes
    if line_count >= 3 or len(content) > 150:
        display = f"[Pasted ~{line_count} lines]" if line_count >= 3 else f"[Pasted {len(content)} chars]"
        return PasteResult(kind="summary", text=normalized, display=display)

    # Small paste — insert directly
    return PasteResult(kind="text", text=normalized, display=normalized)


def detect_file_path(text: str, repo_path: str | None = None) -> PasteResult | None:
    """Check if the text looks like a file path and read it."""
    text = text.strip()
    if not text or "\n" in text:
        return None
    if len(text) > 500:
        return None

    # Try as absolute or relative path
    candidates: list[Path] = []
    p = Path(text)
    candidates.append(p)
    if repo_path and not p.is_absolute():
        candidates.append(Path(repo_path) / p)

    for candidate in candidates:
        if candidate.is_file():
            return read_local_file(str(candidate))

    return None


def read_local_file(path: str) -> PasteResult:
    """Read a local file and return as a PasteResult."""
    p = Path(path)
    try:
        content = p.read_bytes()
        mime = _guess_mime(p.suffix)

        if mime.startswith("image/"):
            return PasteResult(
                kind="image",
                filename=p.name,
                mime=mime,
                content=content,
                display=f"[Image: {p.name}]",
            )

        if mime.startswith("text/") or mime == "application/octet-stream":
            text = content.decode("utf-8", errors="replace")
            return PasteResult(
                kind="file",
                filename=p.name,
                mime=mime,
                text=text,
                display=f"[File: {p.name}]",
            )

        return PasteResult(
            kind="binary",
            filename=p.name,
            mime=mime,
            content=content,
            display=f"[Binary: {p.name}]",
        )
    except (OSError, PermissionError):
        return None


def _guess_mime(suffix: str) -> str:
    """Guess MIME type from file extension."""
    mapping = {
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".toml": "text/x-toml",
        ".html": "text/html",
        ".css": "text/css",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
    }
    return mapping.get(suffix.lower(), "application/octet-stream")
