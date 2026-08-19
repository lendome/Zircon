"""Parallel execution of read-only tool batches + DDG circuit breaker."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.executor import Executor
from zirconAgent.tools.web_ops import _CircuitBreaker


class Call:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class SlowRegistry:
    """Each execution takes `delay`s; records concurrency."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def safe_execute(self, name: str, arguments: dict) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        return f"{name}:{arguments.get('query', arguments.get('path', ''))}"


def _make_executor(registry) -> Executor:
    ex = Executor.__new__(Executor)
    ex.registry = registry
    return ex


class TestParallelBatch(unittest.TestCase):
    def test_read_only_batch_runs_concurrently(self) -> None:
        registry = SlowRegistry(delay=0.05)
        ex = _make_executor(registry)
        calls = [
            Call("web_search", {"query": "a"}),
            Call("fetch_url", {"url": "https://x.dev"}),
            Call("read_file", {"path": "f.py"}),
        ]
        t0 = time.monotonic()
        results = asyncio.run(ex._execute_batch(calls))
        elapsed = time.monotonic() - t0

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0], "web_search:a")  # order preserved
        self.assertEqual(registry.max_active, 3)  # truly concurrent
        self.assertLess(elapsed, 0.12)  # ~1x delay, not 3x

    def test_mutating_batch_runs_sequentially(self) -> None:
        registry = SlowRegistry(delay=0.02)
        ex = _make_executor(registry)
        calls = [
            Call("read_file", {"path": "f.py"}),
            Call("edit_file", {"path": "f.py"}),
        ]
        asyncio.run(ex._execute_batch(calls))
        self.assertEqual(registry.max_active, 1)

    def test_single_call_stays_sequential(self) -> None:
        registry = SlowRegistry(delay=0.01)
        ex = _make_executor(registry)
        asyncio.run(ex._execute_batch([Call("web_search", {"query": "solo"})]))
        self.assertEqual(registry.max_active, 1)

    def test_exception_in_batch_becomes_error_string(self) -> None:
        class ExplodingRegistry:
            async def safe_execute(self, name: str, arguments: dict) -> str:
                if name == "fetch_url":
                    raise RuntimeError("boom")
                return "ok"

        ex = _make_executor(ExplodingRegistry())
        calls = [Call("web_search", {"query": "a"}), Call("fetch_url", {"url": "u"})]
        results = asyncio.run(ex._execute_batch(calls))
        self.assertEqual(results[0], "ok")
        self.assertIn("Error executing fetch_url", results[1])


class TestToolFailureStreak(unittest.TestCase):
    def _ex(self) -> Executor:
        return _make_executor(SlowRegistry())

    def test_intervention_after_three_failures(self) -> None:
        ex = self._ex()
        r1 = ex._record_tool_outcome("web_search", "Search backend is cooling down")
        r2 = ex._record_tool_outcome("web_search", "Search timed out — blocked")
        self.assertIsNone(r1)
        self.assertIsNone(r2)
        r3 = ex._record_tool_outcome("web_search", "Error searching the web: x")
        self.assertIsNotNone(r3)
        self.assertIn("STOP calling `web_search`", r3)

    def test_fires_once_per_streak(self) -> None:
        ex = self._ex()
        for _ in range(2):
            ex._record_tool_outcome("web_search", "Error: fail")
        self.assertIsNotNone(ex._record_tool_outcome("web_search", "Error: fail"))
        # 4th consecutive failure: no repeat intervention
        self.assertIsNone(ex._record_tool_outcome("web_search", "Error: fail"))

    def test_success_resets_streak(self) -> None:
        ex = self._ex()
        ex._record_tool_outcome("web_search", "Error: fail")
        ex._record_tool_outcome("web_search", "Error: fail")
        ex._record_tool_outcome("web_search", "1. Good Result\n   https://x.dev")
        self.assertIsNone(ex._record_tool_outcome("web_search", "Error: fail"))

    def test_streaks_tracked_per_tool(self) -> None:
        ex = self._ex()
        ex._record_tool_outcome("web_search", "Error: a")
        ex._record_tool_outcome("fetch_url", "Error: b")
        ex._record_tool_outcome("web_search", "Error: a")
        self.assertIsNone(ex._record_tool_outcome("fetch_url", "Error: b"))
        self.assertIsNotNone(ex._record_tool_outcome("web_search", "Error: a"))

    def test_normal_results_never_flagged(self) -> None:
        self.assertFalse(Executor._result_is_failure("1. Result\n  https://x.dev"))
        self.assertFalse(Executor._result_is_failure("file contents here"))
        self.assertTrue(Executor._result_is_failure("Error executing tool"))
        self.assertTrue(Executor._result_is_failure("HTTP 502: bad gateway"))


class TestSynthesisContextBudget(unittest.TestCase):
    def test_history_trimmed_to_fit_and_no_orphan_tool_messages(self) -> None:
        import asyncio
        from zirconAgent.core.types import TIER_PRESETS, Tier
        from zirconAgent.subagents.researcher import ResearcherSubAgent

        captured: dict = {}

        class FakeRouter:
            async def generate(self, role, messages, tools=None, max_tokens=0, disable_reasoning=False):
                captured["messages"] = messages
                class R: content = "FINAL ANSWER: X"
                return R()

        class FakeResult:
            # 30 turns of 5k chars each = 150k chars, far over the 40k budget
            history_turns = []
            for i in range(15):
                history_turns.append({"role": "assistant", "content": f"turn {i}", "tool_calls": []})
                history_turns.append({"role": "tool", "tool_call_id": str(i), "content": "x" * 5000})

        sub = ResearcherSubAgent(FakeRouter(), None, ".", tier_config=TIER_PRESETS[Tier.BALANCED])
        out = asyncio.run(sub._synthesize_final(
            [{"role": "system", "content": "sys"}], FakeResult(), "answer now", False
        ))
        self.assertEqual(out, "FINAL ANSWER: X")

        sent = captured["messages"]
        total_chars = sum(len(str(m.get("content") or "")) for m in sent)
        self.assertLess(total_chars, 45_000)
        # First kept history message must not be an orphaned tool message
        history_part = sent[1:-1]
        if history_part:
            self.assertNotEqual(history_part[0]["role"], "tool")


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self) -> None:
        cb = _CircuitBreaker(threshold=3, cooldown=60.0)
        self.assertFalse(cb.is_open)
        cb.record_block()
        cb.record_block()
        self.assertFalse(cb.is_open)
        cb.record_block()
        self.assertTrue(cb.is_open)
        self.assertGreater(cb.seconds_remaining, 50)

    def test_success_resets(self) -> None:
        cb = _CircuitBreaker(threshold=2, cooldown=60.0)
        cb.record_block()
        cb.record_block()
        self.assertTrue(cb.is_open)
        cb.record_success()
        self.assertFalse(cb.is_open)

    def test_cooldown_expires(self) -> None:
        cb = _CircuitBreaker(threshold=1, cooldown=0.05)
        cb.record_block()
        self.assertTrue(cb.is_open)
        time.sleep(0.06)
        self.assertFalse(cb.is_open)


if __name__ == "__main__":
    unittest.main()
