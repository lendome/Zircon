"""
Editor integration & external tools.

Bidirectional integration with external editors (VS Code, Zed, $EDITOR):
  - Discover running editor connections via lock files
  - Read the user's current file/selection and auto-attach as context
  - Open $EDITOR for longer prompt editing with renderer suspend/resume
  - Push mentions from editor (right-click → send to CLI)
  - Track selection state (pending/sent/dismissed)
  - Reconcile extmark positions after external edits
"""

from __future__ import annotations

from .connection import EditorConnection, discover_editor_connection
from .selection import EditorSelection, SelectionState, SelectionTracker
from .integration import EditorIntegration

__all__ = [
    "EditorConnection",
    "discover_editor_connection",
    "EditorSelection",
    "SelectionState",
    "SelectionTracker",
    "EditorIntegration",
]
