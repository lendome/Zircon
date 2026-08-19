"""
Session navigation — parent/child session trees for subagents.

Subagent tasks create child sessions. Navigation commands:
  - first child:  go to first child session
  - next child:   cycle to next sibling
  - prev child:   cycle to previous sibling
  - go parent:    go to parent session
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SessionNode:
    """A node in the session tree."""

    id: str
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)


class SessionTree:
    """A tree of sessions with parent/child relationships."""

    def __init__(self) -> None:
        self._nodes: dict[str, SessionNode] = {}

    def add(self, session_id: str, parent_id: str | None = None) -> None:
        node = SessionNode(id=session_id, parent_id=parent_id)
        self._nodes[session_id] = node
        if parent_id and parent_id in self._nodes:
            if session_id not in self._nodes[parent_id].children:
                self._nodes[parent_id].children.append(session_id)

    def get(self, session_id: str) -> SessionNode | None:
        return self._nodes.get(session_id)

    def get_children(self, session_id: str) -> list[str]:
        node = self._nodes.get(session_id)
        return node.children if node else []

    def get_parent(self, session_id: str) -> str | None:
        node = self._nodes.get(session_id)
        return node.parent_id if node else None

    def get_siblings(self, session_id: str) -> list[str]:
        node = self._nodes.get(session_id)
        if not node or not node.parent_id:
            return []
        parent = self._nodes.get(node.parent_id)
        if not parent:
            return []
        return [c for c in parent.children if c != session_id]


class SessionNavigation:
    """
    Navigate the session tree.

    Commands:
      - first_child(): go to first child session
      - next_child(dir): cycle siblings
      - go_parent(): go to parent session
    """

    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree
        self._current: str = ""
        self._navigate: Callable[[str], None] | None = None

    def set_current(self, session_id: str) -> None:
        self._current = session_id

    def set_navigate_handler(self, handler: Callable[[str], None]) -> None:
        self._navigate = handler

    def _navigate_to(self, session_id: str) -> None:
        if self._navigate is not None:
            self._navigate(session_id)
            self._current = session_id

    def first_child(self) -> bool:
        """Go to the first child session."""
        children = self._tree.get_children(self._current)
        # Filter to actual child sessions (parent_id != None)
        child_sessions = [c for c in children if self._tree.get_parent(c) is not None]
        if len(child_sessions) <= 0:
            return False
        self._navigate_to(child_sessions[0])
        return True

    def next_child(self, direction: int = 1) -> bool:
        """Cycle to the next/previous sibling."""
        siblings = self._tree.get_siblings(self._current)
        if not siblings:
            return False
        # Find current position and wrap around
        try:
            idx = siblings.index(self._current)
        except ValueError:
            return False
        next_idx = (idx + direction) % len(siblings)
        self._navigate_to(siblings[next_idx])
        return True

    def go_parent(self) -> bool:
        """Go to the parent session."""
        parent_id = self._tree.get_parent(self._current)
        if parent_id:
            self._navigate_to(parent_id)
            return True
        return False
