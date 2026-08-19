from __future__ import annotations

import re

from .base import BaseSubAgent
from ..llm.prompts import SYSTEM_RESEARCHER

# A final line that announces intent instead of delivering results
_INTENT_TAIL = re.compile(
    r"\b(let me|i'll|i will|next,? i|now i(?:'ll| will)?|i need to|trying a?)\b",
    re.IGNORECASE,
)

# Structured decomposition: constraints + hypotheses + hop chain, BEFORE any
# searching. Modeled on how strong researchers actually solve these: extract
# the hard filters, generate candidate answers from internal knowledge,
# eliminate against the constraints, and only use search to VERIFY — not to
# discover from scratch.
_PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Plan the research: constraints, candidate hypotheses, and the verification chain.",
    "parameters": {
        "type": "object",
        "properties": {
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Every hard filter in the question, as a checklist — the "
                    "exact qualifiers and boundaries it specifies. The final "
                    "answer must satisfy ALL of them."
                ),
            },
            "key_unknown": {
                "type": "string",
                "description": (
                    "The single pivotal entity that, once identified, makes "
                    "everything else an easy lookup. This is what the research "
                    "must actually crack."
                ),
            },
            "hypotheses": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "3-6 candidate answers for the key unknown from YOUR OWN "
                    "KNOWLEDGE, across different regions/eras/genres — each "
                    "with a one-clause reason it might fit. It is fine if "
                    "none survive; they anchor verification."
                ),
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ordered verification chain: check hypotheses against the "
                    "most discriminating constraints first, then chase the "
                    "final deliverable once the key unknown is confirmed."
                ),
            },
        },
        "required": ["constraints", "key_unknown", "steps"],
    },
}
_PLAN_TOOL_CHOICE = {"type": "function", "function": {"name": "submit_plan"}}

_DECOMPOSE_PROMPT = """\
Plan how to solve this research question. Do NOT answer it yet.

1. CONSTRAINTS: list every hard filter as a checklist item.
2. KEY UNKNOWN: name the single pivotal entity that unlocks everything else \
(usually the final detail is easy to look up ONCE you know the entity — the \
real puzzle is identifying the entity).
3. HYPOTHESES: from your own knowledge, list 3-6 candidate entities across \
DIFFERENT regions, languages, eras, or genres that might fit, each with a \
one-clause reason. Cast a wide net — do not assume the answer is Western or \
English-language.
4. STEPS: the ordered verification chain.

QUESTION:
{question}

Call submit_plan."""


class ResearcherSubAgent(BaseSubAgent):
    system_prompt = SYSTEM_RESEARCHER

    @property
    def tool_names(self) -> list[str]:
        return ["web_search", "fetch_url", "lookup_docs", "run_command"]

    def turn_budget(self) -> int | None:
        # Web research needs more search-fetch-reason iterations than code
        # steps: 10 turns on the low tier, 20 balanced, 30 quality.
        return self.tier.effective_research_max_turns()

    def deadline_seconds(self) -> float | None:
        # Hard 5-minute wall-clock cap, enforced even mid-LLM-call. Research
        # past this is thrashing; the loop stops and answers from the evidence.
        return 300.0

    def model_role(self) -> str:
        # Dedicated 'research' profile so the research model can be swapped
        # independently of the main coding model (falls back to default).
        return "research"

    async def _decompose(self, task: str) -> dict:
        """Produce constraints + hypotheses + verification chain via a forced
        function call. Returns {} on any failure so research proceeds
        unscaffolded rather than breaking.
        """
        import asyncio
        for attempt in range(3):
            try:
                resp = await self.router.generate(
                    role=self.model_role(),
                    messages=[{"role": "user", "content": _DECOMPOSE_PROMPT.format(question=task)}],
                    tools=[_PLAN_TOOL],
                    tool_choice=_PLAN_TOOL_CHOICE,
                    max_tokens=2048,
                )
                for call in resp.tool_calls:
                    if call.name == "submit_plan" and isinstance(call.arguments, dict):
                        def _clean(key: str, cap: int) -> list[str]:
                            items = call.arguments.get(key) or []
                            if not isinstance(items, list):
                                return []
                            return [str(s).strip() for s in items if str(s).strip()][:cap]
                        plan = {
                            "constraints": _clean("constraints", 10),
                            "hypotheses": _clean("hypotheses", 8),
                            "steps": _clean("steps", 8),
                            "key_unknown": str(call.arguments.get("key_unknown") or "").strip(),
                        }
                        if plan["steps"] or plan["constraints"]:
                            return plan
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
        return {}

    def _plan_context(self, plan: dict) -> str:
        parts = ["## RESEARCH PLAN (your roadmap)"]
        if plan.get("constraints"):
            parts.append(
                "CONSTRAINT CHECKLIST — the answer must satisfy ALL of these; "
                "use them to ELIMINATE candidates:"
            )
            parts.extend(f"  [{i}] {c}" for i, c in enumerate(plan["constraints"], 1))
        if plan.get("key_unknown"):
            parts.append(
                f"\nKEY UNKNOWN: {plan['key_unknown']}\n"
                "Crack this first — once identified, the remaining details are "
                "easy lookups anchored to it."
            )
        if plan.get("hypotheses"):
            parts.append("\nCANDIDATE HYPOTHESES (from prior knowledge — verify, don't trust):")
            parts.extend(f"  - {h}" for h in plan["hypotheses"])
        if plan.get("steps"):
            parts.append("\nVERIFICATION CHAIN (work in order, revise as you learn):")
            parts.extend(f"  {i}. {s}" for i, s in enumerate(plan["steps"], 1))
        parts.append(
            "\nMETHOD: verify hypotheses against constraints — search to VERIFY "
            "a candidate, not to discover from scratch. When a candidate fails "
            "a constraint, eliminate it and say so. If searches return junk, "
            "that is EVIDENCE your framing is wrong (wrong region, language, "
            "era, or genre) — generate NEW hypotheses from a different frame "
            "instead of rewording the query. Once the key unknown is confirmed, "
            "anchor all remaining searches to it by name."
        )
        return "\n".join(parts) + "\n"

    async def run(self, task: str, context: str = "", **kwargs):
        # Scaffold: constraints + hypotheses + chain, pinned into context.
        plan = await self._decompose(task)
        if plan:
            plan_text = self._plan_context(plan)
            context = f"{plan_text}\n{context}" if context else plan_text
        return await super().run(task, context=context, **kwargs)

    def continuation_nudge(self, task: str, output: str) -> str | None:
        out = (output or "").strip()
        if not out:
            return (
                "You returned no answer. Continue your research and deliver "
                "your findings (or your best answer with the evidence so far) now."
            )
        # The task demands an explicit FINAL ANSWER line and it's missing
        if "FINAL ANSWER" in task and "FINAL ANSWER" not in out:
            return (
                "You stopped without the required FINAL ANSWER line. Continue "
                "researching if needed, then END your response with:\n"
                "FINAL ANSWER: <the exact answer>\nCONFIDENCE: <0-100>\n"
                "If you cannot determine the answer, give your best guess."
            )
        # Short output ending in narrated intent ("Let me try...") is a
        # give-up mid-plan, not an answer.
        last_line = out.splitlines()[-1]
        if len(out) < 400 and _INTENT_TAIL.search(last_line):
            return (
                "You stopped mid-plan without delivering results. Execute the "
                "approach you just described and report your findings, or give "
                "your best answer based on what you found."
            )
        return None
