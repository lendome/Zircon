"""
Paste normalization — handle CRLF/CR from different terminals.

Windows ConPTY often sends CR-only newlines in bracketed paste.
Different terminals send pastes differently — normalize to LF.
"""

from __future__ import annotations


def normalize_paste(raw: str) -> str:
    """Normalize pasted text: CRLF → LF, remaining CR → LF."""
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def normalize_prompt_content(content: str) -> str:
    """Normalize prompt content from editor output.

    Handles Windows line endings from editor output:
    - Strips trailing CRLF or LF if it's the only newline
    """
    if content.endswith("\r\n"):
        body = content[:-2]
        if "\n" not in body and "\r" not in body:
            return body
    if content.endswith("\n"):
        body = content[:-1]
        if "\n" not in body and "\r" not in body:
            return body
    return content
