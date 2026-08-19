from __future__ import annotations

from ..base import BaseSubAgent
from ...llm.prompts import SYSTEM_SWARM_COORDINATOR


class CoordinatorSwarmAgent(BaseSubAgent):
    system_prompt = SYSTEM_SWARM_COORDINATOR

    @property
    def tool_names(self) -> list[str]:
        return ["read_file", "grep_code", "find_symbols", "get_structure", "glob_files", "list_dir", "edit_file", "edit_lines", "run_command"]