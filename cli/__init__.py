"""
Zircon CLI — thin entry point, daemon-backed architecture, fat TUI.

This package implements the CLI+TUI split architecture:

    packages/cli  (this package)
      ├── index.py      — thin binary entry: parse args, manage daemon, launch TUI
      ├── spec.py       — declarative command spec tree
      ├── runtime.py    — walks spec tree, lazy-loads handlers
      ├── commands/     — command handlers (lazy-loaded)
      ├── daemon/       — backend server, lifecycle, transport
      └── tui/          — full-screen reactive UI with provider tree

Usage:
    python -m zirconAgent.cli                    # launch TUI (default)
    python -m zirconAgent.cli serve              # start daemon server
    python -m zirconAgent.cli task "fix the bug" # headless task
    python -m zirconAgent.cli service start      # start background daemon
    python -m zirconAgent.cli service stop       # stop background daemon
"""

from __future__ import annotations

from .index import main

__all__ = ["main"]
