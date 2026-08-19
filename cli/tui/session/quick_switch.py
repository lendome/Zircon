"""
Quick switch slots — pin sessions to numbered slots (1-9) for instant switching.

Keybindings: <leader>1 through <leader>9
"""

from __future__ import annotations

from typing import Any, Callable


class QuickSwitchSlots:
    """
    Manages numbered session slots for quick switching.

    - pin(slot, session_id): assign a session to a slot
    - switch(slot): navigate to the session in a slot
    - get(slot): get the session ID in a slot
    """

    NUM_SLOTS = 9

    def __init__(self) -> None:
        self._slots: dict[int, str] = {}  # slot -> session_id
        self._navigate: Callable[[str], None] | None = None

    def set_navigate_handler(self, handler: Callable[[str], None]) -> None:
        """Set the function called when switching (navigates to session)."""
        self._navigate = handler

    def pin(self, slot: int, session_id: str) -> None:
        """Pin a session to a numbered slot (1-9)."""
        if 1 <= slot <= self.NUM_SLOTS:
            self._slots[slot] = session_id

    def unpin(self, slot: int) -> None:
        """Remove a session from a slot."""
        self._slots.pop(slot, None)

    def switch(self, slot: int) -> bool:
        """Switch to the session pinned in a slot. Returns True if switched."""
        session_id = self._slots.get(slot)
        if session_id is None:
            return False
        if self._navigate is not None:
            self._navigate(session_id)
            return True
        return False

    def get(self, slot: int) -> str | None:
        """Get the session ID in a slot."""
        return self._slots.get(slot)

    def get_all(self) -> dict[int, str]:
        """Get all slot assignments."""
        return dict(self._slots)

    def clear(self) -> None:
        self._slots.clear()
