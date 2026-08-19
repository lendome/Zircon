"""
Editor selection tracking — state management for editor context.

States:
  - pending:   selection exists, not yet sent
  - sent:      selection was sent with a prompt
  - dismissed: user dismissed this selection

The selection appears as a chip in the prompt footer and is sent
as a system reminder with the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SelectionState(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DISMISSED = "dismissed"


@dataclass
class EditorSelection:
    """A file selection from the editor."""

    file: str = ""
    ranges: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_range(self) -> bool:
        return any(r.get("text") for r in self.ranges)

    @property
    def selection_key(self) -> tuple[str, tuple[str, ...]]:
        """A unique key for this selection (file + range labels)."""
        labels = tuple(r.get("label", "") for r in self.ranges)
        return (self.file, labels)

    @property
    def file_label(self) -> str:
        """Format the file as a short label (dir/index.ts for index files)."""
        if not self.file:
            return ""
        from pathlib import Path
        p = Path(self.file)
        name = p.name
        # Show "dir/index.ts" instead of just "index.ts"
        import re
        if re.match(r"^index\.\w+$", name):
            return f"{p.parent.name}/{name}"
        return name

    @property
    def range_label(self) -> str:
        """Format the selection range as a label."""
        if not self.ranges:
            return ""
        labels: list[str] = []
        for r in self.ranges:
            if not r.get("text"):
                continue
            start = r.get("start_line", r.get("start", "?"))
            end = r.get("end_line", r.get("end", "?"))
            if start == end:
                labels.append(f"#{start}")
            else:
                labels.append(f"#{start}-{end}")
        return ", ".join(labels)

    @property
    def full_label(self) -> str:
        """Combined file + range label."""
        label = self.file_label
        range_label = self.range_label
        if range_label:
            return f"{label}{range_label}"
        return label


class SelectionTracker:
    """
    Tracks editor selection state.

    - set_selection(): update the current selection (resets state to pending)
    - mark_sent(): mark the current selection as sent
    - dismiss(): mark as dismissed (won't show again)
    - clear(): remove the current selection
    """

    def __init__(self) -> None:
        self._selection: EditorSelection | None = None
        self._state: SelectionState = SelectionState.PENDING
        self._dismissed_key: tuple[str, tuple[str, ...]] | None = None

    @property
    def selection(self) -> EditorSelection | None:
        if self._selection is None:
            return None
        if self._state == SelectionState.DISMISSED:
            return None
        if self._dismissed_key == self._selection.selection_key:
            return None
        return self._selection

    @property
    def state(self) -> SelectionState:
        return self._state

    @property
    def is_pending(self) -> bool:
        return self._state == SelectionState.PENDING and self.selection is not None

    @property
    def label(self) -> str:
        sel = self.selection
        return sel.full_label if sel else ""

    def set_selection(self, selection: EditorSelection) -> None:
        """Update the current selection (resets state to pending)."""
        if self._dismissed_key == selection.selection_key:
            return
        self._selection = selection
        self._state = SelectionState.PENDING

    def mark_sent(self) -> None:
        """Mark the current selection as sent."""
        self._state = SelectionState.SENT

    def dismiss(self) -> None:
        """Dismiss the current selection."""
        if self._selection is not None:
            self._dismissed_key = self._selection.selection_key
        self._state = SelectionState.DISMISSED

    def clear(self) -> None:
        """Remove the current selection."""
        self._selection = None
        self._state = SelectionState.PENDING

    def preserve_from_new_session(self) -> None:
        """Reset to pending when starting a new session (if not dismissed)."""
        if self._selection is not None and self._state != SelectionState.DISMISSED:
            self._state = SelectionState.PENDING
