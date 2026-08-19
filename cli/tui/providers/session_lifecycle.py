"""
SessionLifecycleProvider — holds the SessionLifecycle instance.

Provides session list, create, fork, share, compact, rename, delete
operations through the transport.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..session.lifecycle import SessionLifecycle
from ..session.quick_switch import QuickSwitchSlots
from ..session.revert import RevertManager
from ..session.navigation import SessionNavigation, SessionTree
from ..session.title import TerminalTitleManager
from .base import Provider


class SessionLifecycleProvider(Provider):
    name = "session_lifecycle"

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = workspace

    def provide(self, registry: ContextRegistry) -> Any:
        transport = registry.get("sdk") if registry.has("sdk") else None

        lifecycle = SessionLifecycle(transport=transport, workspace=self._workspace)
        ctx = Context(name=self.name)
        ctx.set(lifecycle)
        registry.register(ctx)

        # Quick switch slots
        quick_switch = QuickSwitchSlots()
        qs_ctx = Context(name="quick_switch")
        qs_ctx.set(quick_switch)
        registry.register(qs_ctx)

        # Revert manager
        revert = RevertManager()
        rv_ctx = Context(name="revert")
        rv_ctx.set(revert)
        registry.register(rv_ctx)

        # Session tree + navigation
        tree = SessionTree()
        nav = SessionNavigation(tree)
        nav_ctx = Context(name="session_nav")
        nav_ctx.set(nav)
        registry.register(nav_ctx)

        tree_ctx = Context(name="session_tree")
        tree_ctx.set(tree)
        registry.register(tree_ctx)

        # Terminal title manager
        title_mgr = TerminalTitleManager()
        title_ctx = Context(name="terminal_title")
        title_ctx.set(title_mgr)
        registry.register(title_ctx)

        return lifecycle
