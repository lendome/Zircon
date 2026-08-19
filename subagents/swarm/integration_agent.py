from __future__ import annotations

from ..base import BaseSubAgent
from ...llm.prompts import SYSTEM_SWARM_COORDINATOR


class IntegrationSwarmAgent(BaseSubAgent):
    system_prompt = SYSTEM_SWARM_COORDINATOR + """

## ADDITIONAL RULES FOR INTEGRATION
- Do NOT redo work that individual tracks already completed.
- Focus ONLY on cross-track issues: mismatched imports, inconsistent API contracts,
  missing shared type definitions, conflicting file paths.
- If a major integration gap exists that requires rework, flag it clearly.
- Apply minimal targeted edits to resolve each issue.
"""

    @property
    def tool_names(self) -> list[str]:
        return ["read_file", "grep_code", "edit_file", "edit_lines", "create_file", "run_command"]