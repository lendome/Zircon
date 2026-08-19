from __future__ import annotations

import asyncio
import logging

from .types import TierConfig
from ..llm.router import ModelRouter
from ..llm.prompts import SYSTEM_ADVISOR, SYSTEM_ADVISOR_CHECKIN

logger = logging.getLogger("agent.core.advisor")


class Advisor:
    """Large 'manager' model that produces a structured Execution Plan for the
    smaller worker model to follow (Advisor-Agent pattern).

    The advisor never answers the user's request itself — it only deconstructs
    it into objective, tone/style, step-by-step instructions, and constraints.
    The resulting plan is injected into the agent's context as a note, so the
    whole feature degrades to a no-op when disabled or when the call fails.
    """

    def __init__(self, router: ModelRouter, tier_config: TierConfig | None = None):
        self.router = router
        self.tier = tier_config or TierConfig(name="balanced")
        # Model id of the most recent advisor call (for UI display).
        self.last_model: str = ""

    def profile_model(self) -> str:
        """Model id the advisor role resolves to (before any call is made)."""
        try:
            candidates = self.router.select("advisor")
            return candidates[0].model if candidates else ""
        except Exception:
            return ""

    def _track_model(self, response_model: str) -> None:
        self.last_model = response_model or self.profile_model()

    async def advise(self, task: str, context_summary: str = "") -> str | None:
        """Generate an Execution Plan for the given task.

        Returns the plan text, or None when the advisor is disabled or the
        model produced no usable output. Exceptions propagate to the caller
        (which wraps this in a safe helper).
        """
        if not self.tier.advisor_enabled:
            return None

        context_block = context_summary[: self.tier.advisor_context_max_chars] if context_summary else ""
        prompt = SYSTEM_ADVISOR
        if context_block:
            prompt += f"\n## PROJECT CONTEXT\n{context_block}\n"

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"User's Request:\n{task}"},
        ]
        response = await asyncio.wait_for(
            self.router.generate(
                role="advisor",
                messages=messages,
                max_tokens=self.tier.advisor_max_tokens,
            ),
            timeout=60,
        )
        self._track_model(response.model or "")
        plan_text = (response.content or "").strip()
        if not plan_text:
            logger.warning("Advisor returned an empty plan")
            return None
        logger.info("Advisor plan generated (%d chars)", len(plan_text))
        return plan_text

    async def check_in(self, task: str, turn: int, trajectory_digest: str) -> str | None:
        """Mid-loop feedback memo: approvals, criticisms, ideas, must-fix items.

        Called by the executor every `advisor_checkin_interval` turns. Returns
        the feedback text, or None when disabled or the model produced nothing
        usable. Exceptions propagate to the caller (which wraps this safely).
        """
        if not self.tier.advisor_enabled:
            return None

        messages = [
            {"role": "system", "content": SYSTEM_ADVISOR_CHECKIN},
            {"role": "user", "content": (
                f"Original task:\n{task}\n\n"
                f"Agent activity digest (after turn {turn}):\n{trajectory_digest}"
            )},
        ]
        response = await asyncio.wait_for(
            self.router.generate(
                role="advisor",
                messages=messages,
                max_tokens=self.tier.advisor_max_tokens,
            ),
            timeout=60,
        )
        self._track_model(response.model or "")
        feedback = (response.content or "").strip()
        if not feedback:
            logger.warning("Advisor check-in returned empty feedback (turn %d)", turn)
            return None
        logger.info("Advisor check-in at turn %d (%d chars)", turn, len(feedback))
        return feedback
