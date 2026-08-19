"""
Input editing bindings — full Emacs-style text editing support.

Provides cursor movement, word operations, undo/redo, selection, and
line manipulation for the prompt textarea.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .definitions import Definitions


@dataclass
class InputAction:
    """A single text-editing action."""

    name: str
    handler: Callable[["InputBindings"], None]
    description: str = ""


class InputBindings:
    """
    Manages text-editing state and operations for the prompt input.

    Supports:
    - Cursor movement (left, right, home, end, word forward/backward)
    - Deletion (char, word, to-line-end, to-line-start, entire line)
    - Undo/redo with history stack
    - Selection (select all, word, line)
    - Multi-line support (newline insertion)
    """

    def __init__(self) -> None:
        self._text: str = ""
        self._cursor: int = 0
        self._selection_start: int | None = None
        self._undo_stack: list[tuple[str, int, int | None]] = []
        self._redo_stack: list[tuple[str, int, int | None]] = []
        self._max_history = 100

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._push_undo()
        self._text = value
        self._cursor = min(self._cursor, len(value))

    @property
    def cursor(self) -> int:
        return self._cursor

    def set_cursor(self, pos: int) -> None:
        """Move the cursor to an absolute offset (clamped), dropping any selection."""
        self._cursor = max(0, min(pos, len(self._text)))
        self._selection_start = None

    def set_selection(self, anchor: int, cursor: int) -> None:
        """Set a selection from absolute text offsets, as used by mouse drag."""
        anchor = max(0, min(anchor, len(self._text)))
        cursor = max(0, min(cursor, len(self._text)))
        self._cursor = cursor
        self._selection_start = anchor if anchor != cursor else None

    def set_text(self, text: str, cursor: int | None = None) -> None:
        self._push_undo()
        self._text = text
        self._cursor = cursor if cursor is not None else len(text)

    def insert(self, text: str) -> None:
        """Insert text at cursor position, replacing any active selection."""
        self._push_undo()
        if self._selection_start is not None:
            start = min(self._selection_start, self._cursor)
            end = max(self._selection_start, self._cursor)
            self._text = self._text[:start] + text + self._text[end:]
            self._cursor = start + len(text)
            self._selection_start = None
            return
        self._text = self._text[:self._cursor] + text + self._text[self._cursor:]
        self._cursor += len(text)

    def delete(self, start: int, end: int) -> str:
        """Delete text between start and end. Returns deleted text."""
        self._push_undo()
        deleted = self._text[start:end]
        self._text = self._text[:start] + self._text[end:]
        self._cursor = start
        self._selection_start = None
        return deleted

    # ── Cursor movement ──────────────────────────────────────────────

    def move_left(self) -> None:
        self._selection_start = None
        self._cursor = max(0, self._cursor - 1)

    def move_right(self) -> None:
        self._selection_start = None
        self._cursor = min(len(self._text), self._cursor + 1)

    def move_home(self) -> None:
        self._selection_start = None
        line_start = self._text.rfind("\n", 0, self._cursor)
        self._cursor = line_start + 1 if line_start >= 0 else 0

    def move_end(self) -> None:
        self._selection_start = None
        line_end = self._text.find("\n", self._cursor)
        self._cursor = line_end if line_end >= 0 else len(self._text)

    def move_word_forward(self) -> None:
        self._selection_start = None
        self._cursor = self._word_forward_offset(self._cursor)

    def move_word_backward(self) -> None:
        self._selection_start = None
        self._cursor = self._word_backward_offset(self._cursor)

    def _word_forward_offset(self, i: int) -> int:
        while i < len(self._text) and self._text[i].isspace():
            i += 1
        while i < len(self._text) and not self._text[i].isspace():
            i += 1
        return i

    def _word_backward_offset(self, i: int) -> int:
        i -= 1
        while i > 0 and self._text[i].isspace():
            i -= 1
        while i > 0 and not self._text[i - 1].isspace():
            i -= 1
        return max(0, i)

    # ── Shift-selection ──────────────────────────────────────────────

    def _extend_selection(self, new_cursor: int) -> None:
        """Move the cursor, keeping/growing the selection anchor."""
        if self._selection_start is None:
            self._selection_start = self._cursor
        self._cursor = new_cursor
        # Collapse an empty selection back to a plain cursor
        if self._selection_start == self._cursor:
            self._selection_start = None

    def select_left(self) -> None:
        self._extend_selection(max(0, self._cursor - 1))

    def select_right(self) -> None:
        self._extend_selection(min(len(self._text), self._cursor + 1))

    def select_home(self) -> None:
        line_start = self._text.rfind("\n", 0, self._cursor)
        self._extend_selection(line_start + 1 if line_start >= 0 else 0)

    def select_end(self) -> None:
        line_end = self._text.find("\n", self._cursor)
        self._extend_selection(line_end if line_end >= 0 else len(self._text))

    def select_word_forward(self) -> None:
        self._extend_selection(self._word_forward_offset(self._cursor))

    def select_word_backward(self) -> None:
        self._extend_selection(self._word_backward_offset(self._cursor))

    # ── Deletion ─────────────────────────────────────────────────────

    def delete_char_forward(self) -> None:
        if self._selection_start is not None:
            self._delete_selection()
            return
        if self._cursor < len(self._text):
            self._push_undo()
            self._text = self._text[:self._cursor] + self._text[self._cursor + 1:]

    def delete_char_backward(self) -> None:
        if self._selection_start is not None:
            self._delete_selection()
            return
        if self._cursor > 0:
            self._push_undo()
            self._text = self._text[:self._cursor - 1] + self._text[self._cursor:]
            self._cursor -= 1

    def _delete_selection(self) -> None:
        start = min(self._selection_start, self._cursor)
        end = max(self._selection_start, self._cursor)
        self._push_undo()
        self._text = self._text[:start] + self._text[end:]
        self._cursor = start
        self._selection_start = None

    def delete_word_forward(self) -> None:
        if self._selection_start is not None:
            self._delete_selection()
            return
        start = self._cursor
        end = self._word_forward_offset(start)
        if end > start:
            self._push_undo()
            self._text = self._text[:start] + self._text[end:]
            self._cursor = start

    def delete_word_backward(self) -> None:
        if self._selection_start is not None:
            self._delete_selection()
            return
        end = self._cursor
        start = self._word_backward_offset(end)
        if start < end:
            self._push_undo()
            self._text = self._text[:start] + self._text[end:]
            self._cursor = start

    def delete_to_line_end(self) -> None:
        line_end = self._text.find("\n", self._cursor)
        end = line_end if line_end >= 0 else len(self._text)
        if end > self._cursor:
            self._push_undo()
            self._text = self._text[:self._cursor] + self._text[end:]

    def delete_to_line_start(self) -> None:
        line_start = self._text.rfind("\n", 0, self._cursor)
        start = line_start + 1 if line_start >= 0 else 0
        if start < self._cursor:
            self._push_undo()
            self._text = self._text[:start] + self._text[self._cursor:]
            self._cursor = start

    def delete_line(self) -> None:
        self._push_undo()
        line_start = self._text.rfind("\n", 0, self._cursor)
        start = line_start + 1 if line_start >= 0 else 0
        line_end = self._text.find("\n", self._cursor)
        end = (line_end + 1) if line_end >= 0 else len(self._text)
        self._text = self._text[:start] + self._text[end:]
        self._cursor = start

    # ── Undo/redo ────────────────────────────────────────────────────

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append((self._text, self._cursor, self._selection_start))
        self._text, self._cursor, self._selection_start = self._undo_stack.pop()
        self._clamp_state()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append((self._text, self._cursor, self._selection_start))
        self._text, self._cursor, self._selection_start = self._redo_stack.pop()
        self._clamp_state()

    def _clamp_state(self) -> None:
        self._cursor = max(0, min(self._cursor, len(self._text)))
        if self._selection_start is not None:
            self._selection_start = max(0, min(self._selection_start, len(self._text)))
            if self._selection_start == self._cursor:
                self._selection_start = None

    def _push_undo(self) -> None:
        self._undo_stack.append((self._text, self._cursor, self._selection_start))
        self._redo_stack.clear()
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)

    # ── Selection ────────────────────────────────────────────────────

    @property
    def has_selection(self) -> bool:
        return self._selection_start is not None

    def select_all(self) -> None:
        self._selection_start = 0
        self._cursor = len(self._text)

    def get_selection(self) -> str | None:
        if self._selection_start is None:
            return None
        start = min(self._selection_start, self._cursor)
        end = max(self._selection_start, self._cursor)
        return self._text[start:end]

    def get_selection_bounds(self) -> tuple[int, int] | None:
        """(start, end) offsets of the active selection, or None."""
        if self._selection_start is None:
            return None
        return (
            min(self._selection_start, self._cursor),
            max(self._selection_start, self._cursor),
        )

    def clear_selection(self) -> None:
        self._selection_start = None

    # ── Submit ────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return len(self._text.strip()) == 0

    def submit(self) -> str:
        """Return the text and clear the input."""
        text = self._text
        self._text = ""
        self._cursor = 0
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._selection_start = None
        return text


def get_input_binding_definitions() -> dict[str, str]:
    """Return all input-editing binding names and their defaults."""
    input_names = [
        "input_move_left", "input_move_right", "input_line_home",
        "input_line_end", "input_word_forward", "input_word_backward",
        "input_delete_char_forward", "input_delete_char_backward",
        "input_delete_word_forward", "input_delete_word_backward",
        "input_delete_to_line_end", "input_delete_to_line_start",
        "input_delete_line", "input_undo", "input_redo",
        "input_select_all", "input_select_left", "input_select_right",
        "input_select_word_forward", "input_select_word_backward",
        "input_select_home", "input_select_end",
        "input_newline", "input_submit",
    ]
    return {name: Definitions[name].default for name in input_names if name in Definitions}


INPUT_ACTION_MAP: dict[str, str] = {
    "input_move_left": "move_left",
    "input_move_right": "move_right",
    "input_line_home": "move_home",
    "input_line_end": "move_end",
    "input_word_forward": "move_word_forward",
    "input_word_backward": "move_word_backward",
    "input_delete_char_forward": "delete_char_forward",
    "input_delete_char_backward": "delete_char_backward",
    "input_delete_word_forward": "delete_word_forward",
    "input_delete_word_backward": "delete_word_backward",
    "input_delete_to_line_end": "delete_to_line_end",
    "input_delete_to_line_start": "delete_to_line_start",
    "input_delete_line": "delete_line",
    "input_undo": "undo",
    "input_redo": "redo",
    "input_select_all": "select_all",
    "input_select_left": "select_left",
    "input_select_right": "select_right",
    "input_select_word_forward": "select_word_forward",
    "input_select_word_backward": "select_word_backward",
    "input_select_home": "select_home",
    "input_select_end": "select_end",
}


def dispatch_input_action(bindings: InputBindings, action_name: str) -> bool:
    """Dispatch an input editing action. Returns True if handled."""
    method_name = INPUT_ACTION_MAP.get(action_name)
    if method_name is None:
        return False
    method = getattr(bindings, method_name, None)
    if method is not None:
        method()
        return True
    return False
