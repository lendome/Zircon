"""
Epilogue manager — text printed after the TUI exits.

Lets the shell see what session was active when the TUI exited.
"""

from __future__ import annotations

import sys
from typing import Any


class EpilogueManager:
    """Manages epilogue text printed after TUI exit."""

    def __init__(self) -> None:
        self._epilogue: str | None = None
        self._reason: str | None = None

    def set_epilogue(self, text: str) -> None:
        self._epilogue = text

    def set_reason(self, reason: str) -> None:
        self._reason = reason

    @property
    def epilogue(self) -> str | None:
        return self._epilogue

    @property
    def reason(self) -> str | None:
        return self._reason

    def print(self) -> None:
        """Print epilogue and reason to stdout/stderr."""
        if self._reason:
            print(self._reason, file=sys.stderr)
        if self._epilogue:
            print(self._epilogue, file=sys.stdout)
