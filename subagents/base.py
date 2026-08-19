from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from ..core.types import (
    CompletionDisposition,
    SubAgentProgress,
    SubAgentResult,
    TaskStatus,
    TierConfig,
    ToolCall,
)
from ..core.loop_detector import LoopDetector
from ..llm.router import ModelRouter
from ..tools.registry import ToolRegistry
from ..core.executor import Executor


# Internal control-flow messages that must NEVER escape as a real answer.
# Substring match, lowercased.
_INTERNAL_STOP_MARKERS = (
    "reached the maximum number of tool turns",
    "reached max tool turns",
    "stopped the tool loop",
    "max_turns",
)


def _is_internal_stop(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(m in low for m in _INTERNAL_STOP_MARKERS)


_BUDGET_EXHAUSTED_FALLBACK = (
    "Unable to determine the answer within the research budget."
)

logger = logging.getLogger("agent.subagents.base")

ProgressCallback = Callable[[SubAgentProgress], None]


class BaseSubAgent(ABC):
    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    @property
    @abstractmethod
    def tool_names(self) -> list[str]: ...

    def turn_budget(self) -> int | None:
        """Tool-turn budget for this subagent. None uses the tier default.

        Subagents with structurally different iteration needs (e.g. web
        research) override this to claim a larger or smaller budget.
        """
        return None

    def continuation_nudge(self, task: str, output: str) -> str | None:
        """Return a follow-up prompt if `output` doesn't actually complete
        `task` (e.g. the model narrated a plan instead of answering). The
        run loop sends it once and takes the continued result. None accepts
        the output as-is."""
        return None

    def deadline_seconds(self) -> float | None:
        """Wall-clock cap for this subagent's tool loop. None = no time cap
        (bounded only by the turn budget). Research overrides this."""
        return None

    def model_role(self) -> str:
        """Router role for this subagent's LLM calls. Override to use a
        dedicated profile (e.g. the researcher uses 'research')."""
        return "default"

    def __init__(self, router: ModelRouter, registry: ToolRegistry, repo_path: str, tier_config: TierConfig | None = None):
        self.router = router
        self.registry = registry
        self.repo_path = repo_path
        self.tier = tier_config or TierConfig(name="balanced")
        self._fallback_loop_detector = LoopDetector(
            window_size=self.tier.loop_detection_window,
            max_repetitions=self.tier.loop_max_repetitions,
            stagnation_threshold=self.tier.loop_stagnation_threshold,
        )

    async def run(
        self,
        task: str,
        context: str,
        disable_reasoning: bool = False,
        overall_task: str = "",
        previous_results_summary: str = "",
        step_index: int = 0,
        total_steps: int = 1,
        files_modified_count: int = 0,
        progress_callback: ProgressCallback | None = None,
        agent_id: str | None = None,
    ) -> SubAgentResult:
        """Run the sub-agent with progress reporting.
        
        Uses Executor under the hood for consistent behavior with the main agent.
        Progress is reported via the optional progress_callback.
        """
        _agent_id = agent_id or self.__class__.__name__.replace("SubAgent", "")
        _agent_type = "subagent"

        def _emit(phase: str, detail: str = "", step: int = 0, **kw):
            if progress_callback:
                progress_callback(SubAgentProgress(
                    agent_id=_agent_id,
                    agent_type=_agent_type,
                    status=TaskStatus.RUNNING,
                    phase=phase,
                    detail=detail,
                    step=step,
                    total_steps=total_steps,
                    **kw,
                ))

        _emit("start", f"Starting {_agent_id}: {task[:100]}")

        briefing_parts = []
        if overall_task:
            briefing_parts.append(f"OVERALL TASK: {overall_task[:200]}")
        if total_steps > 1:
            briefing_parts.append(f"PLAN STEP: {step_index}/{total_steps}")
        if previous_results_summary:
            briefing_parts.append(f"PREVIOUS STEPS: {previous_results_summary[:500]}")
        if files_modified_count > 0:
            briefing_parts.append(f"FILES MODIFIED SO FAR: {files_modified_count}")
        budget = self.turn_budget()
        if budget is not None:
            briefing_parts.append(
                f"TURN BUDGET: you have at most {budget} tool turns — "
                f"stop iterating when consecutive turns add nothing new."
            )
        briefing = ""
        if briefing_parts:
            briefing = "## MISSION BRIEFING\n" + "\n".join(briefing_parts)

        full_context = context
        if briefing:
            full_context = briefing + "\n\n" + (context if context else "")

        messages = [
            {"role": "system", "content": self.system_prompt + ("\n\n" + full_context if full_context else "")},
            {"role": "user", "content": task},
        ]

        tools = self.registry.get_schemas(self.tool_names)
        files_read: list[str] = []
        files_modified: list[str] = []
        deadline = self.deadline_seconds()

        # Use Executor for consistent tool-loop behavior with the main agent
        executor = Executor(self.router, self.registry, tier_config=self.tier, role=self.model_role())

        _emit("generating", "Calling LLM...", step=step_index)

        try:
            result = await executor.run_tool_loop(
                messages,
                tools=tools,
                max_turns=budget,
                disable_reasoning=disable_reasoning,
                max_seconds=deadline,
            )
        except Exception as e:
            err_msg = str(e)
            _emit("failed", f"LLM error: {err_msg}", step=step_index)
            if "429" in err_msg or "Too Many Requests" in err_msg:
                return SubAgentResult(False, err_msg, files_read, files_modified)
            return SubAgentResult(False, f"LLM error: {e}", files_read, files_modified)

        # Collect file tracking from executor result
        files_read = getattr(result, "files_read", [])
        files_modified = getattr(result, "files_modified", [])

        # One-shot continuation: if the output doesn't complete the task
        # (e.g. the model stopped mid-plan without an answer), nudge it once.
        if result.success:
            nudge = self.continuation_nudge(task, result.output or "")
            if nudge and getattr(result, "disposition", None) == CompletionDisposition.TURN_LIMIT:
                # Out of tool budget with no deliverable. A tool continuation
                # would just hit the limit again — instead force ONE tool-less
                # synthesis over the evidence already gathered.
                _emit("finalizing", "Turn budget exhausted — synthesizing final answer", step=step_index)
                synth = await self._synthesize_final(
                    messages, result, nudge, disable_reasoning
                )
                if synth:
                    result.output = synth
                elif _is_internal_stop(result.output or ""):
                    # Salvage failed and the raw output is an internal control
                    # message — never let that reach the caller as an answer.
                    result.output = _BUDGET_EXHAUSTED_FALLBACK
            elif nudge:
                _emit("continuing", "Output incomplete — nudging for a final answer", step=step_index)
                followup = (
                    messages
                    + list(getattr(result, "history_turns", []) or [])
                    + [{"role": "user", "content": nudge}]
                )
                cont_budget = max(3, (budget or self.tier.effective_subagent_max_turns()) // 3)
                try:
                    cont = await executor.run_tool_loop(
                        followup,
                        tools=tools,
                        max_turns=cont_budget,
                        disable_reasoning=disable_reasoning,
                    )
                except Exception:
                    cont = None
                if cont is not None and cont.success and (cont.output or "").strip():
                    for f in getattr(cont, "files_read", []):
                        if f not in files_read:
                            files_read.append(f)
                    for f in getattr(cont, "files_modified", []):
                        if f not in files_modified:
                            files_modified.append(f)
                    result = cont

        if result.success:
            _emit("complete", f"{_agent_id} completed successfully", step=step_index,
                  files_modified=files_modified, files_read=files_read)
            return SubAgentResult(True, result.output, files_read, files_modified)
        else:
            _emit("failed", f"{_agent_id} failed: {result.output[:200]}", step=step_index)
            # Fall back to original tool-call loop if executor failed
            # (keep original logic as fallback for backward compatibility)
            return await self._fallback_run(
                task, full_context, messages, tools, disable_reasoning,
                files_read, files_modified, _emit, _agent_id,
            )

    async def _synthesize_final(
        self,
        messages: list[dict],
        result: Any,
        nudge: str,
        disable_reasoning: bool,
    ) -> str:
        """One tool-less generation over the gathered evidence.

        Used when the tool budget is exhausted but the output is not a
        deliverable: the model must answer from what it already found.
        """
        final_user_msg = {
            "role": "user",
            "content": (
                "You have used your entire tool budget — no more tool "
                "calls are possible. Based ONLY on the evidence you have "
                "already gathered above: " + nudge
            ),
        }
        history = list(getattr(result, "history_turns", []) or [])

        # The full trajectory can exceed the model's context window (which
        # silently kills the synthesis call) — keep the newest turns that fit
        # a conservative character budget, dropping the oldest first.
        budget_chars = 40_000
        kept: list[dict] = []
        used = sum(len(str(m.get("content") or "")) for m in messages) + len(final_user_msg["content"])
        for turn_msg in reversed(history):
            cost = len(str(turn_msg.get("content") or "")) + 200
            if used + cost > budget_chars:
                break
            kept.append(turn_msg)
            used += cost
        kept.reverse()
        # A tool message without its preceding assistant tool_calls message is
        # invalid — drop orphaned tool messages at the head.
        while kept and kept[0].get("role") == "tool":
            kept.pop(0)

        synth_messages = messages + kept + [final_user_msg]
        # Retry: this fires exactly when the tool budget is spent, and under
        # concurrent load the salvage generation can itself be rate-limited.
        # A single failure must not cause an internal message to leak.
        for attempt in range(3):
            try:
                response = await self.router.generate(
                    role=self.model_role(),
                    messages=synth_messages,
                    max_tokens=self.tier.default_max_tokens,
                    disable_reasoning=disable_reasoning,
                )
                text = (response.content or "").strip()
                if text and not _is_internal_stop(text):
                    return text
            except Exception as e:
                logger.warning("final synthesis attempt %d failed (%d msgs, ~%d chars): %s",
                               attempt + 1, len(synth_messages), used, e)
                await asyncio.sleep(1.5 * (attempt + 1))
        return ""

    async def _fallback_run(
        self,
        task: str,
        full_context: str,
        messages: list[dict],
        tools: list[dict] | None,
        disable_reasoning: bool,
        files_read: list[str],
        files_modified: list[str],
        emit: Callable,
        agent_id: str,
    ) -> SubAgentResult:
        """Fallback: original tool-call loop if Executor fails."""
        logger.info("SubAgent %s using fallback tool-call loop", agent_id)
        emit("fallback", "Executor failed, using fallback loop", step=0)
        self._fallback_loop_detector.reset()
        effective_max = self.turn_budget() or self.tier.effective_subagent_max_turns()
        turn = 0

        while True:
            try:
                response = await self.router.generate(
                    role=self.model_role(),
                    messages=messages,
                    tools=tools,
                    max_tokens=self.tier.default_max_tokens,
                    disable_reasoning=disable_reasoning,
                )
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "Too Many Requests" in err_msg:
                    continue
                return SubAgentResult(False, f"LLM error: {e}", files_read, files_modified)

            if not response.tool_calls:
                return SubAgentResult(True, response.content, files_read, files_modified)

            turn_files_read: list[str] = []
            turn_files_modified: list[str] = []
            for call in response.tool_calls:
                if call.name in ("read_file", "grep_code", "find_symbols", "get_structure", "glob_files", "list_dir"):
                    for key in ("path", "file_path"):
                        if key in call.arguments:
                            p = call.arguments[key]
                            files_read.append(p)
                            turn_files_read.append(p)
                elif call.name in ("edit_file", "edit_lines", "create_file", "delete_file"):
                    if "path" in call.arguments:
                        p = call.arguments["path"]
                        files_modified.append(p)
                        turn_files_modified.append(p)

            # Check for loop patterns: warnings give hints, critical stops a hang.
            loop_check = self._fallback_loop_detector.record(
                response.tool_calls,
                files_read=turn_files_read,
                files_modified=turn_files_modified,
            )
            if loop_check.severity == "critical":
                logger.warning("Fallback loop CRITICAL for %s: %s", agent_id, loop_check.reason)
                return SubAgentResult(
                    True,
                    response.content or f"Stopped fallback loop: {loop_check.reason}",
                    files_read, files_modified,
                )
            if loop_check.severity == "warning":
                warning_msg = (
                    f"<system_note>\n"
                    f"WARNING: {loop_check.reason}\n"
                    f"</system_note>"
                )
                messages.append({"role": "system", "content": warning_msg})
                logger.info("Fallback loop warning for %s: %s", agent_id, loop_check.reason)

            assistant_msg: dict = {
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
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            messages.append(assistant_msg)

            for call in response.tool_calls:
                result_str = await self.registry.execute(call.name, call.arguments)

                from ..core.distiller import Distiller
                distilled = Distiller(tier_config=self.tier).distill_for_history(result_str, call.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": distilled,
                })

            turn += 1
            if turn >= effective_max:
                logger.info("Fallback loop hit max_turns (%d) for %s", effective_max, agent_id)
                output = (response.content or "").strip()
                if not output or _is_internal_stop(output):
                    # Try to salvage an answer from the gathered evidence
                    # rather than leaking an internal turn-limit message.
                    nudge = self.continuation_nudge(task, output)
                    if nudge:
                        stub = SubAgentResult(True, output, files_read, files_modified)
                        stub.history_turns = messages[1:]  # drop the system msg
                        salvaged = await self._synthesize_final(
                            messages[:1], stub, nudge, disable_reasoning
                        )
                        output = salvaged or _BUDGET_EXHAUSTED_FALLBACK
                    else:
                        output = output or _BUDGET_EXHAUSTED_FALLBACK
                return SubAgentResult(True, output, files_read, files_modified)
