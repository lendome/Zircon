"""deep_research tool: delegates to ResearcherSubAgent, is offered to the main
agent, and is kept OUT of the researcher's own toolset (no recursion)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.tools.research_ops import DeepResearchTool
from zirconAgent.subagents.researcher import ResearcherSubAgent


class TestDeepResearchTool(unittest.TestCase):
    def test_schema_and_name(self) -> None:
        tool = DeepResearchTool(None, None, ".", tier_getter=lambda: None)
        self.assertEqual(tool.name, "deep_research")
        self.assertIn("question", tool.schema["required"])
        # Description must steer away from single-fact lookups
        self.assertIn("web_search", tool.description)
        self.assertIn("research", tool.description.lower())

    def test_delegates_to_researcher(self) -> None:
        captured = {}

        async def fake_run(self, question, context="", **kw):
            captured["question"] = question
            captured["context"] = context
            from zirconAgent.core.types import SubAgentResult
            return SubAgentResult(True, "Synthesized findings: pros use X.", [], [])

        orig = ResearcherSubAgent.run
        ResearcherSubAgent.run = fake_run
        try:
            tool = DeepResearchTool(
                router=object(), registry=object(), repo_path=".",
                tier_getter=lambda: None,
            )
            out = asyncio.run(tool.run(
                question="how do professional disassemblers handle SCC efficiently",
                context="from the abstract_control.go file",
            ))
        finally:
            ResearcherSubAgent.run = orig

        self.assertEqual(out, "Synthesized findings: pros use X.")
        self.assertIn("disassemblers", captured["question"])
        self.assertIn("abstract_control", captured["context"])

    def test_errors_are_returned_not_raised(self) -> None:
        async def boom(self, question, context="", **kw):
            raise RuntimeError("network down")

        orig = ResearcherSubAgent.run
        ResearcherSubAgent.run = boom
        try:
            tool = DeepResearchTool(object(), object(), ".", tier_getter=lambda: None)
            out = asyncio.run(tool.run(question="x"))
        finally:
            ResearcherSubAgent.run = orig
        self.assertIn("deep_research failed", out)
        self.assertIn("network down", out)

    def test_not_in_researcher_toolset_no_recursion(self) -> None:
        # The researcher must NOT have deep_research, or it could call itself.
        sub = ResearcherSubAgent(None, None, ".")
        self.assertNotIn("deep_research", sub.tool_names)
        self.assertIn("web_search", sub.tool_names)


if __name__ == "__main__":
    unittest.main()
