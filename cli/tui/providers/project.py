"""
ProjectProvider — holds workspace/project metadata.

Equivalent of OpenCode's ProjectProvider. Knows the workspace path,
project name, and whether it's a git repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class ProjectInfo:
    workspace: str
    name: str
    is_git: bool = False

    @property
    def path(self) -> Path:
        return Path(self.workspace)


class ProjectProvider(Provider):
    name = "project"

    def __init__(self, workspace: str) -> None:
        self._workspace = workspace

    def provide(self, registry: ContextRegistry) -> Any:
        path = Path(self._workspace).resolve()
        info = ProjectInfo(
            workspace=str(path),
            name=path.name or "workspace",
            is_git=(path / ".git").exists(),
        )
        ctx = Context(name=self.name)
        ctx.set(info)
        registry.register(ctx)
        return info
