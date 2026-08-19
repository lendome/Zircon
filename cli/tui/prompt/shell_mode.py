"""
Shell mode — typing `!` at the start of an empty prompt switches to
shell mode. The prompt has a different color and submits via a
shell-command API instead of a regular prompt API.

Escape or backspace-at-position-0 exits shell mode.
"""

from __future__ import annotations

from typing import Callable


class ShellMode:
    """
    Manages shell mode state for the prompt.

    When active:
      - The prompt prefix is `!`
      - The prompt has a different color (typically theme.warning)
      - Submit sends the command to the shell API, not the LLM
    """

    def __init__(self) -> None:
        self.active: bool = False
        self._on_submit: Callable[[str], None] | None = None

    def enter(self) -> None:
        self.active = True

    def exit(self) -> None:
        self.active = False

    def toggle(self) -> None:
        if self.active:
            self.exit()
        else:
            self.enter()

    def set_submit_handler(self, handler: Callable[[str], None]) -> None:
        self._on_submit = handler

    def submit(self, command: str) -> None:
        """Submit a shell command."""
        if self._on_submit is not None:
            self._on_submit(command.lstrip("!").strip())

    def should_enter(self, text: str, cursor: int) -> bool:
        """Check if shell mode should be entered (cursor at 0, empty text)."""
        return cursor == 0 and len(text.strip()) == 0

    def should_exit(self, text: str, cursor: int, key: str) -> bool:
        """Check if shell mode should exit (Escape or backspace at 0)."""
        if key in ("escape", "backspace"):
            return cursor <= 1 and text.startswith("!")
        return False
