"""
Prompt history — navigable history with up/down arrows.

Navigation respects cursor position — pressing up only triggers history
when the cursor is at the start. Each history entry stores the text and
the parts (extmarks) so mentions are restored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HistoryEntry:
    """A single prompt history entry."""

    input: str
    parts: list[dict] = field(default_factory=list)
    timestamp: float = 0.0


class PromptHistory:
    """
    Navigable prompt history.

    Up/Down arrows navigate. Up only triggers when cursor is at position 0.
    Each entry stores text + serialized parts so @mentions are restored.
    """

    def __init__(self, max_entries: int = 100) -> None:
        self._entries: list[HistoryEntry] = []
        self._index: int = -1
        self._max = max_entries
        self._draft: HistoryEntry | None = None

    def add(self, text: str, parts: list[dict] | None = None, timestamp: float = 0.0) -> None:
        """Add a new history entry."""
        import time
        entry = HistoryEntry(
            input=text,
            parts=parts or [],
            timestamp=timestamp or time.time(),
        )
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]
        self._index = -1
        self._draft = None

    def previous(self, current_text: str) -> HistoryEntry | None:
        """Move to the previous (older) history entry.

        Saves the current draft so navigating back restores it.
        Returns the entry or None if at the beginning.
        """
        if not self._entries:
            return None
        if self._index == -1:
            self._draft = HistoryEntry(input=current_text)
            self._index = len(self._entries) - 1
        elif self._index > 0:
            self._index -= 1
        else:
            return None
        return self._entries[self._index]

    def next(self) -> HistoryEntry | None:
        """Move to the next (newer) history entry.

        Returns the entry, or the saved draft if at the end.
        """
        if self._index == -1:
            return None
        if self._index < len(self._entries) - 1:
            self._index += 1
            return self._entries[self._index]
        # At the end — restore the draft
        self._index = -1
        return self._draft

    def reset(self) -> None:
        """Reset navigation to the end (current draft)."""
        self._index = -1
        self._draft = None

    @property
    def entries(self) -> list[HistoryEntry]:
        return list(self._entries)

    def search(self, query: str) -> list[HistoryEntry]:
        """Search history entries by substring."""
        q = query.lower()
        return [e for e in self._entries if q in e.input.lower()]
