"""
EditorIntegrationProvider — holds the EditorIntegration instance.

Provides editor connection discovery, selection tracking, and context
formatting to all TUI components.
"""

from __future__ import annotations

from typing import Any

from ..context import Context, ContextRegistry
from ..editor.integration import EditorIntegration
from .base import Provider


class EditorIntegrationProvider(Provider):
    name = "editor_integration"

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = workspace

    def provide(self, registry: ContextRegistry) -> Any:
        integration = EditorIntegration(workspace=self._workspace)
        ctx = Context(name=self.name)
        ctx.set(integration)
        registry.register(ctx)
        return integration
