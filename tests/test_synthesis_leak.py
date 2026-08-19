"""Turn-limit synthesis must never leak internal control messages as answers,
and must retry under transient failure."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.types import TIER_PRESETS, Tier
from zirconAgent.subagents.base import (
    _BUDGET_EXHAUSTED_FALLBACK,
    _is_internal_stop,
)
from zirconAgent.subagents.researcher import ResearcherSubAgent


class _Resp:
    def __init__(self, content):
        self.content = content


class FlakyRouter:
    """Fails `fail_times`, then returns `answer`."""
    def __init__(self, answer, fail_times=0):
        self.answer = answer
        self.fail_times = fail_times
        self.calls = 0

    async def generate(self, role, messages, tools=None, max_tokens=0, disable_reasoning=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("rate limited")
        return _Resp(self.answer)


def _stub_result():
    class R:
        history_turns = [
            {"role": "assistant", "content": "searched", "tool_calls": []},
            {"role": "tool", "tool_call_id": "1", "content": "found evidence about the topic"},
        ]
    return R()


class TestInternalStopDetection(unittest.TestCase):
    def test_catches_both_leak_variants(self) -> None:
        self.assertTrue(_is_internal_stop("Reached the maximum number of tool turns before…"))
        self.assertTrue(_is_internal_stop("Reached max tool turns (30)."))
        self.assertTrue(_is_internal_stop("Stopped the tool loop to prevent a hang"))
        self.assertFalse(_is_internal_stop("FC Krasnodar"))
        self.assertFalse(_is_internal_stop(""))


class TestSynthesisRetry(unittest.TestCase):
    def _sub(self, router):
        return ResearcherSubAgent(router, None, ".", tier_config=TIER_PRESETS[Tier.BALANCED])

    def test_succeeds_first_try(self) -> None:
        router = FlakyRouter("FINAL ANSWER: 1943")
        sub = self._sub(router)
        out = asyncio.run(sub._synthesize_final(
            [{"role": "system", "content": "s"}], _stub_result(), "answer now", False
        ))
        self.assertEqual(out, "FINAL ANSWER: 1943")
        self.assertEqual(router.calls, 1)

    def test_retries_then_succeeds(self) -> None:
        router = FlakyRouter("FINAL ANSWER: 1943", fail_times=2)
        sub = self._sub(router)
        out = asyncio.run(sub._synthesize_final(
            [{"role": "system", "content": "s"}], _stub_result(), "answer now", False
        ))
        self.assertEqual(out, "FINAL ANSWER: 1943")
        self.assertEqual(router.calls, 3)

    def test_never_returns_internal_message(self) -> None:
        # Even if the model echoes the internal string, synthesis rejects it
        router = FlakyRouter("Reached the maximum number of tool turns")
        sub = self._sub(router)
        out = asyncio.run(sub._synthesize_final(
            [{"role": "system", "content": "s"}], _stub_result(), "answer now", False
        ))
        self.assertEqual(out, "")  # rejected → caller substitutes fallback

    def test_all_attempts_fail_returns_empty(self) -> None:
        router = FlakyRouter("x", fail_times=99)
        sub = self._sub(router)
        out = asyncio.run(sub._synthesize_final(
            [{"role": "system", "content": "s"}], _stub_result(), "answer now", False
        ))
        self.assertEqual(out, "")


class TestBenchmarkTraceIsolation(unittest.TestCase):
    def test_concurrent_questions_get_isolated_trajectories(self) -> None:
        from zirconAgent.benchmark import browsecomp as bc

        class FakeRegistry:
            async def safe_execute(self, name, arguments, **kw):
                await asyncio.sleep(0.01)
                return f"result:{arguments.get('q')}"

        reg = FakeRegistry()
        bc._install_trace_hook(reg)

        async def one_question(label, n):
            calls: list = []
            token = bc._TRACE_CALLS.set(calls)
            try:
                for i in range(n):
                    await reg.safe_execute("web_search", {"q": f"{label}-{i}"})
            finally:
                bc._TRACE_CALLS.reset(token)
            return label, calls

        async def main():
            return await asyncio.gather(
                one_question("A", 3),
                one_question("B", 5),
                one_question("C", 2),
            )

        results = dict(asyncio.run(main()))
        # Each question's trajectory contains only its own calls
        self.assertEqual(len(results["A"]), 3)
        self.assertEqual(len(results["B"]), 5)
        self.assertEqual(len(results["C"]), 2)
        self.assertTrue(all("A-" in c["args"]["q"] for c in results["A"]))
        self.assertTrue(all("B-" in c["args"]["q"] for c in results["B"]))


if __name__ == "__main__":
    unittest.main()
