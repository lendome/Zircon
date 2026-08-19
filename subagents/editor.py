from __future__ import annotations

import json

from .base import BaseSubAgent
from ..core.types import SubAgentResult
from ..llm.prompts import SYSTEM_EDITOR


class EditorSubAgent(BaseSubAgent):
    system_prompt = SYSTEM_EDITOR

    @property
    def tool_names(self) -> list[str]:
        return ["read_file", "edit_file", "edit_lines", "aider_edit", "create_file", "delete_file"]

    async def run(self, task: str, context: str, **kwargs) -> "SubAgentResult":
        messages = [
            {"role": "system", "content": self.system_prompt + ("\n\n" + context if context else "")},
            {"role": "user", "content": task},
        ]

        tools = self.registry.get_schemas(self.tool_names)
        files_read: list[str] = []
        files_modified: list[str] = []
        force_edit_given = False

        for _ in range(self.max_turns):
            try:
                response = await self.router.generate(
                    role="default",
                    messages=messages,
                    tools=tools,
                    max_tokens=self.tier.default_max_tokens,
                )
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "Too Many Requests" in err_msg:
                    continue
                return SubAgentResult(False, f"LLM error: {e}", files_read, files_modified)

            if not response.tool_calls:
                if not files_modified and not force_edit_given:
                    force_edit_given = True
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "system",
                        "content": (
                            "CRITICAL: You MUST call edit_file, edit_lines, or create_file to write code. "
                            "Do NOT output text-only responses. The file contents are provided in context above. "
                            "Use edit_lines or create_file to write the implementation NOW."
                        ),
                    })
                    continue
                if not files_modified:
                    return SubAgentResult(False, response.content or "No edits made", files_read, files_modified)
                return SubAgentResult(True, response.content, files_read, files_modified)

            force_edit_given = False  # reset after a tool call

            messages.append({
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            })

            for call in response.tool_calls:
                result_str = await self.registry.execute(call.name, call.arguments)

                if call.name in ("edit_file", "edit_lines", "create_file", "delete_file"):
                    if "path" in call.arguments:
                        files_modified.append(call.arguments["path"])
                    elif "content" in call.arguments:
                        first_line = call.arguments["content"].split("\n")[0]
                        if first_line.strip():
                            files_modified.append(first_line.strip())
                elif call.name == "read_file":
                    for key in ("path", "file_path"):
                        if key in call.arguments:
                            files_read.append(call.arguments[key])

                from ..core.distiller import Distiller
                distilled = Distiller(tier_config=self.tier).distill_for_history(result_str, call.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": distilled,
                })

        return SubAgentResult(False, "Max turns reached", files_read, files_modified)
