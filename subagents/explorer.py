from __future__ import annotations

from .base import BaseSubAgent
from ..llm.prompts import SYSTEM_EXPLORER


class ExplorerSubAgent(BaseSubAgent):
    system_prompt = SYSTEM_EXPLORER

    @property
    def tool_names(self) -> list[str]:
        return ["read_file", "grep_code", "find_symbols", "get_structure", "glob_files", "list_dir"]
