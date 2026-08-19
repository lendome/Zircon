"""Deep-research tool — delegates a substantial external research question
to the dedicated ResearcherSubAgent (iterative search→fetch→reason loop with
a large budget), rather than answering from a single inline web_search.

The main agent calls this like any other tool. It handles the codebase parts
of a task with file tools and hands the open-ended external-research parts to
a real research agent, then weaves the returned findings into its answer.
"""

from __future__ import annotations

from typing import Any, Callable

from .base import Tool


class DeepResearchTool(Tool):
    def __init__(
        self,
        router: Any,
        registry: Any,
        repo_path: str,
        tier_getter: Callable[[], Any],
    ) -> None:
        self._router = router
        self._registry = registry
        self._repo_path = repo_path
        self._tier_getter = tier_getter

    @property
    def name(self) -> str:
        return "deep_research"

    @property
    def description(self) -> str:
        return (
            "Delegate a substantial EXTERNAL research question to a dedicated "
            "research agent that runs a multi-step search → fetch → reason loop "
            "with a large budget, then returns a synthesized, cited summary. "
            "Use this for open-ended research that needs several sources — e.g. "
            "'how do professional-grade X handle Y', 'compare approaches to Z', "
            "'what is the state of the art in W'. Do NOT use it for a single "
            "quick fact (use web_search) or for anything answerable from the "
            "codebase (use the file/search tools). Prefer this over firing many "
            "raw web_search calls yourself when the question is genuinely a "
            "research task."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The research question, stated fully and self-contained.",
                },
                "context": {
                    "type": "string",
                    "description": "Optional background from the current task the researcher should know.",
                },
            },
            "required": ["question"],
        }

    async def run(self, question: str, context: str = "") -> str:
        # Imported lazily to avoid a circular import (subagents import tools).
        from ..subagents.researcher import ResearcherSubAgent

        sub = ResearcherSubAgent(
            self._router,
            self._registry,
            self._repo_path,
            tier_config=self._tier_getter(),
        )
        try:
            result = await sub.run(question, context=context)
        except Exception as e:
            return f"deep_research failed: {type(e).__name__}: {e}"
        return result.output or "deep_research returned no findings."
