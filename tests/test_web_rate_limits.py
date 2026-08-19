"""Rate limiter, retry-delay parsing, DDG block classification, and the
researcher's answer-forcing continuation nudge (all offline)."""

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

from zirconAgent.tools.web_ops import (
    WebSearchTool,
    _RateLimiter,
    _retry_after_seconds,
)


class TestRateLimiter(unittest.TestCase):
    def test_enforces_min_interval(self) -> None:
        async def scenario() -> float:
            limiter = _RateLimiter(min_interval=0.05)
            await limiter.acquire()
            t0 = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - t0

        elapsed = asyncio.run(scenario())
        self.assertGreaterEqual(elapsed, 0.045)

    def test_backoff_pushes_next_slot(self) -> None:
        async def scenario() -> float:
            limiter = _RateLimiter(min_interval=0.0)
            await limiter.acquire()
            limiter.backoff(0.08)
            t0 = time.monotonic()
            await limiter.acquire()
            return time.monotonic() - t0

        elapsed = asyncio.run(scenario())
        self.assertGreaterEqual(elapsed, 0.07)

    def test_concurrent_acquirers_are_serialized(self) -> None:
        async def scenario() -> list[float]:
            limiter = _RateLimiter(min_interval=0.03)
            stamps: list[float] = []

            async def worker() -> None:
                await limiter.acquire()
                stamps.append(time.monotonic())

            await asyncio.gather(*[worker() for _ in range(3)])
            return sorted(stamps)

        stamps = asyncio.run(scenario())
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        for gap in gaps:
            self.assertGreaterEqual(gap, 0.025)


class TestRetryAfterParsing(unittest.TestCase):
    def test_honors_retry_after(self) -> None:
        self.assertEqual(_retry_after_seconds({"retry-after": "7"}, 0), 7.0)

    def test_honors_brave_ratelimit_reset_first_value(self) -> None:
        # Brave format: "1, 1419704" = seconds until per-second / monthly reset
        self.assertEqual(
            _retry_after_seconds({"x-ratelimit-reset": "1, 1419704"}, 0), 1.0
        )

    def test_caps_excessive_server_values(self) -> None:
        self.assertEqual(_retry_after_seconds({"retry-after": "86400"}, 0), 30.0)

    def test_exponential_fallback(self) -> None:
        self.assertEqual(_retry_after_seconds({}, 0), 2.0)
        self.assertEqual(_retry_after_seconds({}, 1), 6.0)
        self.assertEqual(_retry_after_seconds({}, 2), 18.0)

    def test_garbage_header_falls_back(self) -> None:
        self.assertEqual(_retry_after_seconds({"retry-after": "soon"}, 0), 2.0)


class TestContinuationNudge(unittest.TestCase):
    def _sub(self):
        from zirconAgent.subagents.researcher import ResearcherSubAgent

        return ResearcherSubAgent(None, None, ".")

    def test_missing_final_answer_nudged(self) -> None:
        task = "Who did X?\nFINAL ANSWER: <answer>\nCONFIDENCE: <0-100>"
        nudge = self._sub().continuation_nudge(task, "I found some leads on Wikipedia.")
        self.assertIsNotNone(nudge)
        self.assertIn("FINAL ANSWER", nudge)

    def test_present_final_answer_accepted(self) -> None:
        task = "Who did X?\nFINAL ANSWER: <answer>"
        out = "Research complete.\nFINAL ANSWER: Jane Doe\nCONFIDENCE: 80"
        self.assertIsNone(self._sub().continuation_nudge(task, out))

    def test_empty_output_nudged(self) -> None:
        self.assertIsNotNone(self._sub().continuation_nudge("Research X", ""))

    def test_mid_plan_giveup_nudged(self) -> None:
        out = "Let me try a different search approach."
        self.assertIsNotNone(self._sub().continuation_nudge("Research X", out))

    def test_substantive_answer_accepted(self) -> None:
        out = (
            "Based on the official changelog and two independent posts, the "
            "feature landed in version 2.3 (March 2024). Sources: ..."
        )
        self.assertIsNone(self._sub().continuation_nudge("Research X", out))

    def test_other_subagents_never_nudge(self) -> None:
        from zirconAgent.subagents.explorer import ExplorerSubAgent

        sub = ExplorerSubAgent(None, None, ".")
        self.assertIsNone(sub.continuation_nudge("task", "Let me try again."))


if __name__ == "__main__":
    unittest.main()
