"""
Secondary views — sidebar, diff viewer, and secondary dialogs.

Responsive sidebar, full-featured diff viewer, and a collection of
dialogs: status, debug, theme list, session list, model list, agent
list, MCP toggle, skill, stash, export options, workspace.
"""

from __future__ import annotations

from .sidebar import Sidebar, SidebarState
from .diff_viewer import DiffViewer, DiffStyle, DiffViewerState, DiffFile
from .home import HomeLayout
from .dialogs import (
    StatusDialog,
    DebugDialog,
    ThemeListDialog,
    SessionListDialog,
    ModelDialog,
    AgentDialog,
    McpToggleDialog,
    SkillDialog,
    StashDialog,
    ExportOptionsDialog,
)

__all__ = [
    "Sidebar",
    "SidebarState",
    "DiffViewer",
    "DiffStyle",
    "HomeLayout",
    "StatusDialog",
    "DebugDialog",
    "ThemeListDialog",
    "SessionListDialog",
    "ModelDialog",
    "AgentDialog",
    "McpToggleDialog",
    "SkillDialog",
    "StashDialog",
    "ExportOptionsDialog",
]
