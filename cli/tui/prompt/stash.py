"""
Prompt stash — save and restore prompt drafts, like `git stash`.

Stashed prompts survive route changes — when you navigate away and back,
the draft is preserved via a module-level variable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StashEntry:
    """A stashed prompt draft."""

    input: str
    parts: list[dict] = field(default_factory=list)
    timestamp: float = 0.0


class PromptStash:
    """
    Save/restore prompt drafts.

    - push(): save current draft and clear the prompt
    - pop(): restore the most recent stash
    - list(): show all stashed drafts
    """

    def __init__(self, max_entries: int = 20) -> None:
        self._entries: list[StashEntry] = []
        self._max = max_entries

    def push(self, text: str, parts: list[dict] | None = None) -> None:
        """Save the current prompt draft and clear it."""
        import time
        entry = StashEntry(
            input=text,
            parts=parts or [],
            timestamp=time.time(),
        )
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    def pop(self) -> StashEntry | None:
        """Restore and remove the most recent stash."""
        if not self._entries:
            return None
        return self._entries.pop()

    def peek(self) -> StashEntry | None:
        """Look at the most recent stash without removing it."""
        if not self._entries:
            return None
        return self._entries[-1]

    def list(self) -> list[StashEntry]:
        """Return all stashed drafts."""
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    @property
    def count(self) -> int:
        return len(self._entries)
