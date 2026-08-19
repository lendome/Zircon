"""
Session management & lifecycle.

Sessions are first-class entities with full lifecycle:
  - List, create, fork, share, compact, undo/redo, export, rename, delete
  - Continue-from-last on startup (-c flag)
  - Fork-from-message (from timeline)
  - Quick-switch slots (1-9)
  - Parent/child session trees for subagents
  - Terminal title management
  - Session directory filtering
"""

from __future__ import annotations

from .lifecycle import SessionLifecycle, SessionInfo
from .quick_switch import QuickSwitchSlots
from .revert import RevertManager, RevertState
from .navigation import SessionNavigation, SessionTree
from .title import TerminalTitleManager

__all__ = [
    "SessionLifecycle",
    "SessionInfo",
    "QuickSwitchSlots",
    "RevertManager",
    "RevertState",
    "SessionNavigation",
    "SessionTree",
    "TerminalTitleManager",
]
