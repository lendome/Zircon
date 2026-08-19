"""
Terminal title manager — updates the terminal window title based on route.

  Home route:     "CLI"
  Session route:  "CLI | {session title}" (or "CLI" for default titles)
  Plugin route:   "CLI | {plugin id}"

Toggleable via command, persisted in KV.
"""

from __future__ import annotations

import sys
from typing import Any, Callable


class TerminalTitleManager:
    """
    Manages the terminal window title.

    Uses OSC escape sequences to set the title:
      \x1b]2;{title}\x07  (or \x1b]0;{title}\x07)
    """

    def __init__(self) -> None:
        self._enabled: bool = True
        self._current_title: str = "CLI"
        self._kv: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_title(self) -> str:
        return self._current_title

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self.reset()
        elif self._kv is not None:
            self._kv.set("terminal_title_enabled", enabled)

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    def set_kv_store(self, kv: Any) -> None:
        self._kv = kv
        if kv is not None:
            self._enabled = kv.get("terminal_title_enabled", True)

    def set_title(self, title: str) -> None:
        """Set the terminal window title."""
        if not self._enabled:
            return
        self._current_title = title
        # OSC sequence to set window title
        try:
            sys.stdout.write(f"\x1b]2;{title}\x07")
            sys.stdout.flush()
        except Exception:
            pass

    def set_for_route(self, route_type: str, session_title: str = "", plugin_id: str = "") -> None:
        """Set the title based on the current route."""
        if route_type == "home":
            self.set_title("CLI")
        elif route_type == "session":
            if not session_title or self._is_default_title(session_title):
                self.set_title("CLI")
            else:
                title = session_title[:37] + "..." if len(session_title) > 40 else session_title
                self.set_title(f"CLI | {title}")
        elif route_type == "plugin":
            self.set_title(f"CLI | {plugin_id}")
        else:
            self.set_title("CLI")

    @staticmethod
    def _is_default_title(title: str) -> bool:
        return not title or title.startswith("Session ")

    def reset(self) -> None:
        """Reset the title to default."""
        self.set_title("CLI")
