from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolResult(str):
    """Text tool output with optional model-only multimodal content."""

    def __new__(
        cls,
        text: str,
        model_content: list[dict[str, Any]] | None = None,
    ) -> ToolResult:
        value = super().__new__(cls, text)
        value.model_content = model_content or []
        return value


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def schema(self) -> dict[str, Any]: ...

    @abstractmethod
    async def run(self, **kwargs) -> str | ToolResult: ...

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.schema,
        }
