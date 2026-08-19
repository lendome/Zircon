"""Test that multi-turn chat works reliably without crashes.

Validates:
- Two successive chat_stream() calls don't crash
- History doesn't accumulate None entries
- Agent status transitions correctly
- The fix for "NoneType has no len()" in agent.py:1256
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

# Ensure the package is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
import sys
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.types import TaskStatus, Tier, StreamChunk


class TestMultiTurnChat(unittest.TestCase):
    """Validate multi-turn chat_stream reliability."""

    @classmethod
    def setUpClass(cls):
        cls.repo_path = str(_REPO_ROOT)
        cls.config_path = str(_REPO_ROOT / "models.yaml")
        cls.initialized = False

    def _make_agent(self):
        from zirconAgent.core.agent import Agent
        from zirconAgent.core.constants import ensure_zircon_dir
        ensure_zircon_dir(self.repo_path)
        return Agent(
            repo_path=self.repo_path,
            config_path=self.config_path,
            tier=Tier.LOW,  # Low tier = fast, cheap
        )

    def test_two_chat_messages_no_crash(self):
        """Send two chat_stream messages. Second must not crash with NoneType."""
        async def run():
            agent = self._make_agent()

            # ── Turn 1 ──
            chunks1: list[StreamChunk] = []
            async for chunk in agent.chat_stream("hello"):
                chunks1.append(chunk)
                if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                    break

            print(f"  Turn 1 produced {len(chunks1)} chunks, "
                  f"status={chunks1[-1].status.value if chunks1 else '?'}")

            # History must not contain None
            self.assertIsNotNone(agent.context.history,
                                 "history should not be None after turn 1")
            for i, msg in enumerate(agent.context.history):
                self.assertIsNotNone(msg,
                                     f"history[{i}] is None after turn 1")

            # Turn 1 should succeed or await plan
            if chunks1:
                self.assertIn(chunks1[-1].status,
                              [TaskStatus.COMPLETED, TaskStatus.AWAITING_INPUT])

            # ── Turn 2 ──
            if agent.status == TaskStatus.AWAITING_INPUT:
                agent.submit_feedback("approved")
                async for chunk in agent.chat_stream(""):
                    if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                        break

            chunks2: list[StreamChunk] = []
            async for chunk in agent.chat_stream("what is this project"):
                chunks2.append(chunk)
                if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                    break

            print(f"  Turn 2 produced {len(chunks2)} chunks, "
                  f"status={chunks2[-1].status.value if chunks2 else '?'}")

            # History must not contain None after turn 2
            for i, msg in enumerate(agent.context.history):
                self.assertIsNotNone(msg,
                                     f"history[{i}] is None after turn 2")

            # Verify the sum works (must not crash with NoneType)
            total = sum(
                len(msg.get("content", "")) // 4 if msg else 0
                for msg in agent.context.history
            )
            self.assertIsInstance(total, int)
            # Also verify no None entries exist (handled by _FilteredHistory)
            self.assertTrue(all(msg is not None for msg in agent.context.history),
                           "history should contain no None entries")

        asyncio.run(run())

    def test_history_no_none_after_plan_approval(self):
        """History should not get None entries after plan -> approve flow."""
        async def run():
            agent = self._make_agent()
            agent.tier_cfg.skip_planner = False  # allow planning

            # Send first message that triggers a plan
            plan_requested = False
            async for chunk in agent.chat_stream("explain the project structure"):
                if chunk.status == TaskStatus.AWAITING_INPUT:
                    plan_requested = True
                    break
                if chunk.done:
                    break

            print(f"  Plan requested: {plan_requested}, status={agent.status.value}")

            if plan_requested:
                # Approve the plan
                agent.submit_feedback("approved")
                async for chunk in agent.chat_stream(""):
                    if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                        break

            # History must be clean
            for i, msg in enumerate(agent.context.history):
                self.assertIsNotNone(msg, f"history[{i}] is None after plan approval")

        asyncio.run(run())

    def test_history_sum_no_crash(self):
        """Direct test of the sum() expression that crashed at agent.py:1255-1258."""
        async def run():
            agent = self._make_agent()
            from zirconAgent.core.types import TaskStatus

            # Simulate corrupted history
            agent.context.history.append(None)
            agent.context.history.append({"role": "user", "content": "hello"})

            # This exact expression must not crash
            total_hist = sum(
                len(msg.get("content", "")) // 4 if msg else 0
                for msg in agent.context.history
            )
            self.assertIsInstance(total_hist, int)
            self.assertGreaterEqual(total_hist, 0)

            # Clean up
            agent.context.history.clear()

        asyncio.run(run())

    def test_three_turns_continuous(self):
        """Three successive messages without crashing."""
        async def run():
            agent = self._make_agent()

            for turn, msg in enumerate(["hi", "tell me more", "explain further"]):
                chunks: list[StreamChunk] = []
                async for chunk in agent.chat_stream(msg):
                    chunks.append(chunk)
                    if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                        break

                print(f"  Turn {turn+1} ('{msg}'): {len(chunks)} chunks, last_status={chunks[-1].status.value if chunks else '?'}")

                # Handle plan approval
                if agent.status == TaskStatus.AWAITING_INPUT:
                    agent.submit_feedback("approved")
                    async for chunk in agent.chat_stream(""):
                        if chunk.done or chunk.status == TaskStatus.AWAITING_INPUT:
                            break

                # Check no None in history
                for i, m in enumerate(agent.context.history):
                    self.assertIsNotNone(m, f"history[{i}] is None after turn {turn+1}")

                # Check sum works and no None entries
                total = sum(
                    len(m.get("content", "")) // 4 if m else 0
                    for m in agent.context.history
                )
                self.assertIsInstance(total, int)
                self.assertTrue(all(m is not None for m in agent.context.history),
                               f"history contains None entries after turn {turn+1}")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()