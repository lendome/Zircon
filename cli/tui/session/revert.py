"""
Revert management — undo/redo with revert.

Undo reverts to a previous user message and restores the prompt text.
Redo moves forward through the revert history.

Reverted messages are hidden from the conversation flow. A revert
marker appears at the revert point showing what was undone.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RevertState:
    """State of a revert operation."""

    message_id: str = ""
    reverted_count: int = 0
    diff_files: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = 0.0


class RevertManager:
    """
    Manages undo/redo with revert.

    - undo(): revert to the last user message, restore prompt text
    - redo(): move forward through revert history
    - can_undo / can_redo: check if operation is available
    """

    def __init__(self) -> None:
        self._revert: RevertState | None = None
        self._undo_stack: list[RevertState] = []
        self._redo_stack: list[RevertState] = []
        self._on_revert: Callable[[str], Any] | None = None  # async
        self._on_unrevert: Callable[[], Any] | None = None  # async
        self._on_restore_prompt: Callable[[str, list[dict]], None] | None = None

    def set_handlers(
        self,
        on_revert: Callable[[str], Any] | None = None,
        on_unrevert: Callable[[], Any] | None = None,
        on_restore_prompt: Callable[[str, list[dict]], None] | None = None,
    ) -> None:
        """Set callbacks for revert/unrevert/prompt restoration."""
        self._on_revert = on_revert
        self._on_unrevert = on_unrevert
        self._on_restore_prompt = on_restore_prompt

    @property
    def current(self) -> RevertState | None:
        return self._revert

    @property
    def can_undo(self) -> bool:
        return self._revert is not None or bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return self._revert is not None

    async def undo(self, messages: list[dict[str, Any]]) -> bool:
        """Undo to the last user message before the revert point.

        Args:
            messages: List of message dicts with 'id', 'role', 'parts'

        Returns True if the undo was performed.
        """
        revert_point = self._revert.message_id if self._revert else None

        # Find the last user message before the revert point
        user_messages = [
            m for m in messages
            if m.get("role") == "user"
            and (not revert_point or m.get("id", "") < revert_point)
        ]
        if not user_messages:
            return False

        target = user_messages[-1]
        target_id = target.get("id", "")
        if self._revert:
            self._undo_stack.append(self._revert)

        # Call the backend to revert
        if self._on_revert is not None:
            result = self._on_revert(target_id)
            if hasattr(result, "__await__"):
                await result

        self._revert = RevertState(
            message_id=target_id,
            reverted_count=len([m for m in messages if m.get("id", "") >= target_id]),
            timestamp=time.time(),
        )

        # Restore the prompt text from the reverted message's parts
        if self._on_restore_prompt is not None:
            parts = target.get("parts", [])
            text_parts = [p for p in parts if p.get("type") == "text" and not p.get("synthetic")]
            text = "\n".join(p.get("text", "") for p in text_parts)
            file_parts = [p for p in parts if p.get("type") == "file"]
            self._on_restore_prompt(text, file_parts)

        return True

    async def redo(self, messages: list[dict[str, Any]]) -> bool:
        """Redo — move forward through the revert history."""
        if not self._revert:
            return False

        message_id = self._revert.message_id
        # Find the next user message after the revert point
        next_messages = [
            m for m in messages
            if m.get("role") == "user" and m.get("id", "") > message_id
        ]

        if not next_messages:
            # No more messages to redo — unrevert fully
            if self._on_unrevert is not None:
                result = self._on_unrevert()
                if hasattr(result, "__await__"):
                    await result
            self._revert = None
            if self._on_restore_prompt is not None:
                self._on_restore_prompt("", [])
        else:
            next_msg = next_messages[0]
            if self._on_revert is not None:
                result = self._on_revert(next_msg.get("id", ""))
                if hasattr(result, "__await__"):
                    await result
            self._revert = RevertState(
                message_id=next_msg.get("id", ""),
                reverted_count=len([m for m in messages if m.get("id", "") >= next_msg.get("id", "")]),
                timestamp=time.time(),
            )

        return True

    def clear(self) -> None:
        """Clear all revert state."""
        self._revert = None
        self._undo_stack.clear()
        self._redo_stack.clear()
