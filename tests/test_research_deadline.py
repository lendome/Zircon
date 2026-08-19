"""Research has a wall-clock deadline that stops the loop and forces an answer."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.executor import Executor
from zirconAgent.core.types import CompletionDisposition, ToolCall, TierConfig
from zirconAgent.subagents.researcher import ResearcherSubAgent


class SlowRouter:
    """Each generate takes `per_turn`s and always emits a tool call, so the
    loop only stops on the turn cap or the time cap."""
    def __init__(self, per_turn: float):
        self.per_turn = per_turn
        self.calls = 0

    async def generate(self, role, messages, tools=None, max_tokens=0, progress_callback=None, **kw):
        self.calls += 1
        await asyncio.sleep(self.per_turn)
        class R:
            content = ""
            reasoning_content = ""
            tool_calls = [ToolCall("1", "web_search", {"query": "x"})]
        return R()


class StubRegistry:
    repo_path = "."
    async def safe_execute(self, name, arguments, **kw):
        return "some result"


def _executor(router) -> Executor:
    ex = Executor(router, StubRegistry(), tier_config=TierConfig(name="balanced"))
    return ex


class TestResearchDeadline(unittest.TestCase):
    def test_stops_at_time_limit_before_turn_limit(self) -> None:
        # per_turn 0.05s, 100-turn cap, 0.2s deadline → stops on time, ~4 turns
        router = SlowRouter(per_turn=0.05)
        ex = _executor(router)
        result = asyncio.run(ex.run_tool_loop(
            [{"role": "user", "content": "q"}],
            tools=[{"name": "web_search"}],
            max_turns=100,
            max_seconds=0.2,
        ))
        self.assertEqual(result.disposition, CompletionDisposition.TURN_LIMIT)
        self.assertLess(router.calls, 100)  # did not exhaust the turn cap
        self.assertIn("time limit", result.output.lower())

    def test_no_deadline_runs_to_turn_cap(self) -> None:
        router = SlowRouter(per_turn=0.0)
        ex = _executor(router)
        result = asyncio.run(ex.run_tool_loop(
            [{"role": "user", "content": "q"}],
            tools=[{"name": "web_search"}],
            max_turns=3,
            max_seconds=None,
        ))
        # No time cap → stops on the 3-turn cap
        self.assertEqual(router.calls, 3)

    def test_researcher_declares_300s_deadline(self) -> None:
        sub = ResearcherSubAgent(None, None, ".")
        self.assertEqual(sub.deadline_seconds(), 300.0)

    def test_deadline_enforced_mid_call(self) -> None:
        # A single generate() that runs longer than the whole budget must be
        # cut off by the hard cap, not allowed to overrun.
        class HangRouter:
            calls = 0
            async def generate(self, role, messages, tools=None, max_tokens=0, progress_callback=None, **kw):
                HangRouter.calls += 1
                await asyncio.sleep(10)  # far longer than the 0.15s budget
                class R:
                    content = ""; reasoning_content = ""; tool_calls = []
                return R()
        ex = _executor(HangRouter())
        import time as _t
        t0 = _t.monotonic()
        result = asyncio.run(ex.run_tool_loop(
            [{"role": "user", "content": "q"}], tools=[{"name": "web_search"}],
            max_turns=100, max_seconds=0.15,
        ))
        elapsed = _t.monotonic() - t0
        self.assertEqual(result.disposition, CompletionDisposition.TURN_LIMIT)
        self.assertLess(elapsed, 3.0)  # cut off ~mid-call, not after the 10s sleep

    def test_other_subagents_have_no_deadline(self) -> None:
        from zirconAgent.subagents.explorer import ExplorerSubAgent
        self.assertIsNone(ExplorerSubAgent(None, None, ".").deadline_seconds())


if __name__ == "__main__":
    unittest.main()
