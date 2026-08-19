"""
TUI package — full-screen reactive terminal UI with provider tree.

The TUI is a composition of single-responsibility context providers,
each handling one concern (theme, config, routing, SDK connection, etc).
The provider tree is assembled in app.py.
"""

from __future__ import annotations

from .app import run_tui
from .context import Context, create_context, use_context

__all__ = ["run_tui", "Context", "create_context", "use_context"]
