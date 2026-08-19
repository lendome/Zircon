from __future__ import annotations

import asyncio
import re
from typing import Any

from .types import Plan, PlanStep, PlanDecision, TierConfig
from ..llm.router import ModelRouter
from ..llm.prompts import SYSTEM_PLANNER_TEMPLATE, SYSTEM_PLAN_GATEKEEPER
from ..llm.structured import extract_json


_GREETINGS = re.compile(r"^(hi+|hello|hey|greetings|sup|yo|howdy)\b", re.IGNORECASE)
_CLI_COMMANDS = re.compile(r"^/(status|help|reset|exit|approve)\b", re.IGNORECASE)
_INFORMATIONAL = re.compile(r"^(what|how|explain|describe|show|find|where|who|when|why|tell me|list)\b", re.IGNORECASE)
_EDIT_KEYWORDS = {"edit", "modify", "add", "create", "delete", "refactor", "fix", "implement", "change", "update", "rename", "move", "rewrite", "improve", "optimize", "replace", "insert", "remove"}  # add more as needed
# Keywords that suggest a task touches multiple files or has broad scope — definitely needs a plan.
_MULTI_FILE_KEYWORDS = {"migrat", "refactor", "restructur", "redesign", "architect", "overhaul", "end-to-end", "full-stack"}
# Keywords for safety-critical or high-impact changes.
_HIGH_IMPACT_KEYWORDS = {"schema", "database", "api", "endpoint", "migration", "auth", "permiss", "security"}


class PlanGatekeeper:
    def __init__(self, router: ModelRouter, tier_config: TierConfig | None = None):
        self.router = router
        self.tier = tier_config or TierConfig(name="balanced")

    def _rule_decide(self, task: str) -> PlanDecision | None:
        # If plans are disabled, always skip planning
        if getattr(self.tier, 'plans_disabled', False):
            return PlanDecision(needs_plan=False, reason="Plans disabled by configuration")

        t = task.strip()
        if not t:
            return PlanDecision(needs_plan=False, reason="Empty input")

        if _GREETINGS.match(t):
            return PlanDecision(needs_plan=False, reason="Greeting")

        if _CLI_COMMANDS.match(t):
            return PlanDecision(needs_plan=False, reason="CLI command")

        lower = t.lower()
        has_edit = any(kw in lower for kw in _EDIT_KEYWORDS)

        if not has_edit:
            return PlanDecision(needs_plan=False, reason="No edit keywords detected")

        # --- Stricter plan criteria ---
        # 1. Multi-file / high-impact keywords → require plan
        if any(kw in lower for kw in _MULTI_FILE_KEYWORDS):
            return PlanDecision(needs_plan=True, reason="Multi-file/restructuring task requires plan")
        if any(kw in lower for kw in _HIGH_IMPACT_KEYWORDS):
            return PlanDecision(needs_plan=True, reason="High-impact change requires plan")

        # 2. Short simple edits → no plan needed (single file, one-shot)
        #    e.g. "edit README to fix typo" or "add a docstring to foo.py"
        if len(t) < 80 and len(t.split()) < 15:
            return PlanDecision(needs_plan=False, reason="Small single-file edit, no plan needed")

        # 3. Long but single-purpose → still may not need a plan if it's
        #    basically a directed command ("in file X, change Y to Z")
        #    The LLM gatekeeper will decide for ambiguous cases.
        return None

    async def decide(self, task: str, context_summary: str = "") -> PlanDecision:
        # If plans are disabled, always skip planning (fast-path without LLM call)
        if getattr(self.tier, 'plans_disabled', False):
            return PlanDecision(needs_plan=False, reason="Plans disabled by configuration")

        if self.tier.gatekeeper_mode == "rule_only":
            result = self._rule_decide(task)
            if result:
                return result
            lower = task.lower()
            if any(kw in lower for kw in _EDIT_KEYWORDS):
                return PlanDecision(needs_plan=True, reason="Edit keywords present (rule-based)")
            return PlanDecision(needs_plan=False, reason="No plan needed (rule-based)")

        if self.tier.gatekeeper_mode == "hybrid":
            result = self._rule_decide(task)
            if result:
                return result

        prompt = SYSTEM_PLAN_GATEKEEPER + f"\n\nProject context:\n{context_summary or 'No project context available.'}"
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Task: {task}"},
        ]
        response = await asyncio.wait_for(
            self.router.generate(role="planner", messages=messages, max_tokens=self.tier.gatekeeper_max_tokens),
            timeout=60,
        )
        content = response.content.strip()

        if content.startswith("PLAN_REQUIRED"):
            reason = content.split(":", 1)[1].strip() if ":" in content else "Complex task requires planning"
            return PlanDecision(needs_plan=True, reason=reason)
        elif content.startswith("DIRECT_OK"):
            reason = content.split(":", 1)[1].strip() if ":" in content else "Simple task, act directly"
            return PlanDecision(needs_plan=False, reason=reason)
        else:
            lower = task.lower()
            if any(kw in lower for kw in _EDIT_KEYWORDS):
                return PlanDecision(needs_plan=True, reason="Task appears to involve code changes; planning required.")
            return PlanDecision(needs_plan=False, reason="No clear edit intent detected; acting directly.")


class Planner:
    def __init__(self, router: ModelRouter, tier_config: TierConfig | None = None):
        self.router = router
        self.tier = tier_config or TierConfig(name="balanced")

    async def plan(self, task: str, context_summary: str = "",
                   research_summary: str = "") -> Plan:
        """Generate a structured plan.

        Args:
            task: The user's task description.
            context_summary: Repo map / codebase summary for context.
            research_summary: Optional findings from a deep exploration phase
                              (the agent runs this before calling plan()).
        """
        if self.tier.skip_planner:
            return self._lightweight_plan(task)

        # Inject research findings if available so the planner has real context.
        context_block = context_summary or "No project context available."
        if research_summary:
            context_block = (
                f"{context_block}\n\n"
                f"## Research Findings (from deep codebase exploration)\n"
                f"{research_summary[:5000]}"
            )

        prompt = SYSTEM_PLANNER_TEMPLATE.format(context=context_block)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Task: {task}"},
        ]

        role = "architect" if self.tier.name == "quality" else "planner"
        response = await asyncio.wait_for(
            self.router.generate(
                role=role,
                messages=messages,
                max_tokens=self.tier.planner_max_tokens,
            ),
            timeout=120,
        )

        return self._parse_plan(response.content)

    async def replan(
        self,
        task: str,
        context_summary: str,
        failed_plan: Plan,
        failed_step: PlanStep,
    ) -> Plan:
        if self.tier.skip_planner or not self.tier.replanning_enabled:
            return self._lightweight_plan(task)

        messages = [
            {"role": "system", "content": SYSTEM_PLANNER_TEMPLATE.format(context=context_summary)},
            {"role": "user", "content": f"Original task: {task}"},
            {"role": "assistant", "content": f"Previous plan failed at step {failed_step.index}: {failed_step.description}"},
            {"role": "user", "content": "Create a revised plan that addresses the failure. Include exploration steps if needed."},
        ]

        role = "architect" if self.tier.name == "quality" else "planner"
        response = await asyncio.wait_for(
            self.router.generate(role=role, messages=messages, max_tokens=self.tier.planner_max_tokens),
            timeout=120,
        )
        return self._parse_plan(response.content)

    def _lightweight_plan(self, task: str) -> Plan:
        lower = task.lower()
        steps = []
        idx = 0

        steps.append(PlanStep(
            index=idx,
            description="Explore relevant files",
            action="explore",
        ))
        idx += 1

        steps.append(PlanStep(
            index=idx,
            description="Apply the requested changes",
            action="edit",
        ))
        idx += 1

        if self.tier.lightweight_plan_max_steps >= 3:
            steps.append(PlanStep(
                index=idx,
                description="Verify the changes",
                action="verify",
            ))

        return Plan(steps=steps, complexity="simple")

    def _parse_plan(self, content: str) -> Plan:
        data = extract_json(content)
        if data:
            return Plan.from_dict(data)

        return Plan(
            steps=[
                PlanStep(index=0, description="Explore the codebase to understand relevant code", action="explore"),
                PlanStep(index=1, description="Complete the requested task", action="edit"),
                PlanStep(index=2, description="Verify the changes are correct", action="verify"),
            ],
            complexity="moderate",
        )