"""
Editor integration — combines connection discovery, selection tracking,
and context formatting into a single integration layer.

The TUI uses this to:
  - Auto-attach the current editor selection as prompt context
  - Format it as a system reminder for the agent
  - Support mention insertion from the editor
  - Open $EDITOR for prompt editing
"""

from __future__ import annotations

from typing import Any, Callable

from ..reactive.signal import Signal, signal, computed
from .connection import discover_editor_connection, EditorConnection
from .selection import EditorSelection, SelectionTracker, SelectionState


class EditorIntegration:
    """
    Full editor integration.

    Combines:
      - Connection discovery (lock file scanning)
      - Selection tracking (pending/sent/dismissed)
      - Context formatting (system-reminder XML)
      - Mention insertion callback
      - External editor opening (delegates to prompt.editor)
    """

    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace
        self._connection: EditorConnection | None = None
        self._tracker = SelectionTracker()
        self._enabled = signal(True)
        self._on_mention: Callable[[EditorSelection], None] | None = None

    def discover(self) -> EditorConnection | None:
        """Discover an editor connection for the current workspace."""
        self._connection = discover_editor_connection(self.workspace)
        return self._connection

    @property
    def is_connected(self) -> bool:
        return self._connection is not None

    @property
    def connection(self) -> EditorConnection | None:
        return self._connection

    @property
    def tracker(self) -> SelectionTracker:
        return self._tracker

    @property
    def enabled(self) -> Signal[bool]:
        return self._enabled

    def set_selection(self, selection: EditorSelection) -> None:
        """Update the current editor selection."""
        self._tracker.set_selection(selection)

    def set_mention_handler(self, handler: Callable[[EditorSelection], None]) -> None:
        """Register a callback for editor-pushed mentions."""
        self._on_mention = handler

    def push_mention(self, file_path: str, line_start: int | None = None, line_end: int | None = None) -> None:
        """Handle a mention pushed from the editor (e.g., right-click → send to CLI)."""
        selection = EditorSelection(
            file=file_path,
            ranges=[{
                "start_line": line_start,
                "end_line": line_end,
                "text": "",
                "label": f"#{line_start}-{line_end}" if line_start and line_end else "",
            }] if line_start else [],
        )
        if self._on_mention is not None:
            self._on_mention(selection)

    def get_context(self) -> str | None:
        """Get the formatted editor context for the current selection."""
        if not self._enabled.get():
            return None
        sel = self._tracker.selection
        if sel is None:
            return None
        return format_editor_context(sel)

    def dismiss(self) -> None:
        """Dismiss the current editor context."""
        self._tracker.dismiss()

    def mark_sent(self) -> None:
        """Mark the current selection as sent with a prompt."""
        self._tracker.mark_sent()

    def clear(self) -> None:
        self._tracker.clear()

    @property
    def label(self) -> str:
        """Get the current selection label for the prompt footer."""
        return self._tracker.label


def format_editor_context(selection: EditorSelection) -> str | None:
    """Format an editor selection as a system-reminder string."""
    if not selection.file:
        return None

    selected_ranges = [r for r in selection.ranges if r.get("text")]

    if not selected_ranges:
        return (
            f'<system-reminder>Note: The user opened the file "{selection.file}". '
            f"This may or may not be relevant.</system-reminder>"
        )

    parts: list[str] = []
    for i, r in enumerate(selected_ranges):
        prefix = f"Selection {i + 1}: " if len(selected_ranges) > 1 else ""
        start = r.get("start_line", "?")
        end = r.get("end_line", "?")
        if start == end:
            range_label = f"#{start}"
        else:
            range_label = f"#{start}-{end}"
        text = r["text"]
        parts.append(
            f'Note: The user selected {prefix}{range_label} from "{selection.file}". ```{text}```'
        )
    return f"<system-reminder>{' '.join(parts)}\nThis may or may not be relevant.</system-reminder>"
