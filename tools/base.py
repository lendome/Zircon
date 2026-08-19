from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
    async def run(self, **kwargs) -> str: ...

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.schema,
        }
