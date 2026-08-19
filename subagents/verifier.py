from __future__ import annotations

from .base import BaseSubAgent
from ..llm.prompts import SYSTEM_VERIFIER


class VerifierSubAgent(BaseSubAgent):
    system_prompt = SYSTEM_VERIFIER

    @property
    def tool_names(self) -> list[str]:
        return ["run_command", "read_file", "glob_files"]

    async def run(self, task: str, context: str, **kwargs) -> "SubAgentResult":
        result = await super().run(task, context, **kwargs)
        if result.success:
            output_lower = result.output.lower()
            if "fail" in output_lower or "error" in output_lower or "traceback" in output_lower:
                result.success = False
        return result
