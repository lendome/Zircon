"""
Extmarks — virtual text regions tracked separately from plain text.

When you @mention a file or paste content, it becomes an extmark — a
tracked region with styling that doesn't count as typed text. When the
user edits text around an extmark, positions are automatically updated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Extmark:
    """A tracked region in the prompt text."""

    id: int
    start: int
    end: int
    virtual: bool = True
    style: str = ""
    type: str = "prompt_part"
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.end - self.start

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


class ExtmarkManager:
    """
    Manages extmarks in the prompt text.

    Extmarks are created when structured content (file mentions, pastes)
    is inserted. They track positions that shift as the user edits text.
    Orphaned extmarks (whose text was deleted) are cleaned up during sync.
    """

    def __init__(self) -> None:
        self._extmarks: dict[int, Extmark] = {}
        self._next_id: int = 0

    def create(
        self,
        start: int,
        end: int,
        virtual: bool = True,
        style: str = "",
        type: str = "prompt_part",
        data: dict[str, Any] | None = None,
    ) -> Extmark:
        """Create a new extmark. Returns the extmark with assigned id."""
        em = Extmark(
            id=self._next_id,
            start=start,
            end=end,
            virtual=virtual,
            style=style,
            type=type,
            data=data or {},
        )
        self._extmarks[em.id] = em
        self._next_id += 1
        return em

    def get(self, extmark_id: int) -> Extmark | None:
        return self._extmarks.get(extmark_id)

    def get_all(self, type: str | None = None) -> list[Extmark]:
        if type is None:
            return list(self._extmarks.values())
        return [em for em in self._extmarks.values() if em.type == type]

    def remove(self, extmark_id: int) -> None:
        self._extmarks.pop(extmark_id, None)

    def clear(self) -> None:
        self._extmarks.clear()
        self._next_id = 0

    def shift_after(self, offset: int, delta: int) -> None:
        """Shift all extmarks after `offset` by `delta` characters."""
        for em in self._extmarks.values():
            if em.start >= offset:
                em.start += delta
                em.end += delta
            elif em.end > offset:
                em.end += delta

    def reconcile(self, text: str) -> list[int]:
        """Reconcile extmarks with the current text.

        Returns a list of extmark ids that were orphaned (their text
        was deleted) and should be cleaned up.
        """
        orphaned: list[int] = []
        for em_id, em in list(self._extmarks.items()):
            if em.start < 0 or em.end > len(text) or em.start >= em.end:
                orphaned.append(em_id)
                self._extmarks.pop(em_id, None)
            else:
                # Verify the text at the extmark position still matches
                if em.data.get("virtual_text"):
                    expected = em.data["virtual_text"]
                    actual = text[em.start:em.end]
                    if actual != expected:
                        # Try to find the virtual text elsewhere
                        new_start = text.find(expected)
                        if new_start == -1:
                            orphaned.append(em_id)
                            self._extmarks.pop(em_id, None)
                        else:
                            em.start = new_start
                            em.end = new_start + len(expected)
        return orphaned
