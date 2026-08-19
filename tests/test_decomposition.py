"""Research decomposition scaffold: constraints + hypotheses + chain,
pinned into context (hypothesize-then-verify methodology)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.types import ToolCall
from zirconAgent.subagents.researcher import ResearcherSubAgent


class FakeRouter:
    def __init__(self, plan_args=None, raise_exc=None):
        self.plan_args = plan_args
        self.raise_exc = raise_exc
        self.calls = []

    async def generate(self, role, messages, tools=None, tool_choice=None, max_tokens=0, **kw):
        self.calls.append({"tools": tools, "tool_choice": tool_choice})
        if self.raise_exc:
            raise self.raise_exc
        class R:
            pass
        r = R()
        if self.plan_args is not None:
            r.tool_calls = [ToolCall("1", "submit_plan", self.plan_args)]
        else:
            r.tool_calls = []
        r.content = ""
        return r


_FULL_PLAN = {
    "constraints": ["a band, not a solo artist", "THIRD studio album", "released 1980-2000"],
    "key_unknown": "which band and song this is",
    "hypotheses": ["Eraserheads - Ang Huling El Bimbo (Filipino rock, arc matches)",
                   "The Go-Go's (US, has a musical)"],
    "steps": ["verify best hypothesis against constraints", "find the musical", "find the phrase"],
}


class TestDecomposition(unittest.TestCase):
    def _sub(self, router):
        return ResearcherSubAgent(router, None, ".")

    def test_decompose_returns_full_plan(self) -> None:
        router = FakeRouter(plan_args=_FULL_PLAN)
        plan = asyncio.run(self._sub(router)._decompose("q"))
        self.assertEqual(len(plan["constraints"]), 3)
        self.assertEqual(len(plan["hypotheses"]), 2)
        self.assertEqual(len(plan["steps"]), 3)
        self.assertIn("band", plan["key_unknown"])
        self.assertEqual(router.calls[0]["tool_choice"]["function"]["name"], "submit_plan")

    def test_decompose_filters_and_caps(self) -> None:
        args = dict(_FULL_PLAN)
        args["steps"] = ["a", "", "  ", "b"] + [f"s{i}" for i in range(10)]
        args["constraints"] = [f"c{i}" for i in range(15)]
        router = FakeRouter(plan_args=args)
        plan = asyncio.run(self._sub(router)._decompose("q"))
        self.assertNotIn("", plan["steps"])
        self.assertLessEqual(len(plan["steps"]), 8)
        self.assertLessEqual(len(plan["constraints"]), 10)

    def test_decompose_failure_returns_empty(self) -> None:
        router = FakeRouter(raise_exc=RuntimeError("boom"))
        self.assertEqual(asyncio.run(self._sub(router)._decompose("q")), {})

    def test_no_plan_tool_call_returns_empty(self) -> None:
        router = FakeRouter(plan_args=None)
        self.assertEqual(asyncio.run(self._sub(router)._decompose("q")), {})

    def test_empty_plan_returns_empty(self) -> None:
        router = FakeRouter(plan_args={"constraints": [], "steps": [], "key_unknown": ""})
        self.assertEqual(asyncio.run(self._sub(router)._decompose("q")), {})

    def test_plan_context_renders_all_sections(self) -> None:
        sub = self._sub(FakeRouter())
        ctx = sub._plan_context(_FULL_PLAN)
        self.assertIn("CONSTRAINT CHECKLIST", ctx)
        self.assertIn("[1] a band, not a solo artist", ctx)
        self.assertIn("KEY UNKNOWN: which band and song this is", ctx)
        self.assertIn("CANDIDATE HYPOTHESES", ctx)
        self.assertIn("Eraserheads", ctx)
        self.assertIn("VERIFICATION CHAIN", ctx)
        self.assertIn("1. verify best hypothesis", ctx)
        # The methodology coaching must be present
        self.assertIn("VERIFY", ctx)
        self.assertIn("framing is wrong", ctx)

    def test_plan_context_skips_missing_sections(self) -> None:
        sub = self._sub(FakeRouter())
        ctx = sub._plan_context({"constraints": [], "hypotheses": [], "steps": ["only step"], "key_unknown": ""})
        self.assertNotIn("CONSTRAINT CHECKLIST", ctx)
        self.assertNotIn("CANDIDATE HYPOTHESES", ctx)
        self.assertIn("1. only step", ctx)

    def test_run_injects_plan_into_context(self) -> None:
        router = FakeRouter(plan_args=_FULL_PLAN)
        sub = self._sub(router)
        captured = {}

        async def fake_super_run(task, context="", **kw):
            captured["context"] = context
            from zirconAgent.core.types import SubAgentResult
            return SubAgentResult(True, "done", [], [])

        import zirconAgent.subagents.base as base
        orig = base.BaseSubAgent.run
        base.BaseSubAgent.run = lambda self, task, context="", **kw: fake_super_run(task, context, **kw)
        try:
            asyncio.run(sub.run("the question", context="prior notes"))
        finally:
            base.BaseSubAgent.run = orig

        self.assertIn("RESEARCH PLAN", captured["context"])
        self.assertIn("Eraserheads", captured["context"])
        self.assertIn("prior notes", captured["context"])


if __name__ == "__main__":
    unittest.main()
