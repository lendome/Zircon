"""Research anti-thrash detector: many searches without a read → intervention."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.executor import Executor


class Call:
    def __init__(self, name, arguments=None):
        self.name = name
        self.arguments = arguments or {}


def _ex() -> Executor:
    ex = Executor.__new__(Executor)
    return ex


class TestResearchThrash(unittest.TestCase):
    def test_fires_after_five_searches_without_read(self) -> None:
        ex = _ex()
        results = []
        for _ in range(5):
            results.append(ex._record_research_progress([Call("web_search", {"query": "x"})]))
        self.assertEqual(results[:4], [None, None, None, None])
        self.assertIsNotNone(results[4])
        self.assertIn("web searches in a row", results[4])
        self.assertIn("different", results[4].lower())  # reframe guidance

    def test_a_read_resets_the_counter(self) -> None:
        ex = _ex()
        for _ in range(4):
            ex._record_research_progress([Call("web_search")])
        # A fetch resets — no intervention, and the streak restarts
        self.assertIsNone(ex._record_research_progress([Call("fetch_url", {"url": "u"})]))
        for _ in range(4):
            self.assertIsNone(ex._record_research_progress([Call("web_search")]))
        self.assertIsNotNone(ex._record_research_progress([Call("web_search")]))

    def test_batched_searches_count_together(self) -> None:
        ex = _ex()
        # One turn with 5 parallel searches immediately trips it
        out = ex._record_research_progress([Call("web_search") for _ in range(5)])
        self.assertIsNotNone(out)

    def test_refires_every_five(self) -> None:
        ex = _ex()
        fired = [ex._record_research_progress([Call("web_search")]) for _ in range(10)]
        # Interventions at the 5th and 10th search
        self.assertIsNotNone(fired[4])
        self.assertIsNone(fired[5])
        self.assertIsNotNone(fired[9])

    def test_non_research_tools_never_trip(self) -> None:
        ex = _ex()
        for _ in range(10):
            self.assertIsNone(ex._record_research_progress([Call("read_file", {"path": "a.py"})]))
            self.assertIsNone(ex._record_research_progress([Call("grep_code", {"pattern": "x"})]))

    def test_lookup_docs_counts_as_a_read(self) -> None:
        ex = _ex()
        for _ in range(4):
            ex._record_research_progress([Call("web_search")])
        self.assertIsNone(ex._record_research_progress([Call("lookup_docs", {"library": "x"})]))
        # streak reset
        self.assertIsNone(ex._record_research_progress([Call("web_search")]))


class TestSearchGate(unittest.TestCase):
    _TOOLS = [
        {"name": "web_search", "parameters": {}},
        {"name": "fetch_url", "parameters": {}},
        {"name": "lookup_docs", "parameters": {}},
    ]

    def test_gate_arms_when_thrash_fires(self) -> None:
        ex = _ex()
        for _ in range(5):
            ex._record_research_progress([Call("web_search")])
        gates = getattr(ex, "_tool_gates", {})
        self.assertIn("web_search", gates)
        self.assertEqual(gates["web_search"][0], 2)

    def test_gate_strips_web_search_for_two_turns(self) -> None:
        ex = _ex()
        ex._tool_gates = {"web_search": [2, "thrash"]}
        ex._veto_cooldown = {}
        # Turn 1: web_search removed
        t1 = ex._apply_tool_gates(self._TOOLS)
        self.assertNotIn("web_search", [t["name"] for t in t1])
        self.assertIn("fetch_url", [t["name"] for t in t1])
        # Turn 2: still removed
        t2 = ex._apply_tool_gates(self._TOOLS)
        self.assertNotIn("web_search", [t["name"] for t in t2])
        # Turn 3: web_search restored
        t3 = ex._apply_tool_gates(self._TOOLS)
        self.assertIn("web_search", [t["name"] for t in t3])

    def test_no_gate_passes_tools_unchanged(self) -> None:
        ex = _ex()
        ex._tool_gates = {}
        ex._veto_cooldown = {}
        self.assertIs(ex._apply_tool_gates(self._TOOLS), self._TOOLS)

    def test_gate_message_announces_disable(self) -> None:
        ex = _ex()
        msg = None
        for _ in range(5):
            msg = ex._record_research_progress([Call("web_search")])
        self.assertIn("DISABLED", msg)

    def test_reading_during_gate_still_progresses(self) -> None:
        # A fetch during the gate resets the thrash counter (reading is progress)
        ex = _ex()
        for _ in range(5):
            ex._record_research_progress([Call("web_search")])
        self.assertIsNone(ex._record_research_progress([Call("fetch_url", {"url": "u"})]))
        self.assertEqual(ex._searches_since_read, 0)


if __name__ == "__main__":
    unittest.main()
