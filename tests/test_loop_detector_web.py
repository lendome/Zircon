"""Loop detector must not kill iterative web research.

Regression test: web_search's `query` (and fetch_url's `url`) were missing
from the tool-call fingerprint keys, so every search fingerprinted
identically and 5 consecutive search turns tripped the identical-turns
critical stop — killing every BrowseComp research run at ~turn 5.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.loop_detector import LoopDetector, ToolFingerprint


class Call:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class TestWebResearchNotALoop(unittest.TestCase):
    def test_distinct_queries_fingerprint_distinctly(self) -> None:
        a = ToolFingerprint.from_call("web_search", {"query": "first thing"})
        b = ToolFingerprint.from_call("web_search", {"query": "second thing"})
        self.assertNotEqual(a, b)

    def test_distinct_urls_fingerprint_distinctly(self) -> None:
        a = ToolFingerprint.from_call("fetch_url", {"url": "https://a.dev"})
        b = ToolFingerprint.from_call("fetch_url", {"url": "https://b.dev"})
        self.assertNotEqual(a, b)

    def test_iterative_research_is_never_critical(self) -> None:
        det = LoopDetector()
        for i in range(12):  # far beyond identical_turns_critical=5
            check = det.record(
                [Call("web_search", {"query": f"constraint {i} refinement"})],
                files_read=[],
                files_modified=[],
            )
            self.assertNotEqual(
                check.severity, "critical",
                f"turn {i}: productive research flagged critical: {check.reason}",
            )

    def test_search_fetch_alternation_is_never_critical(self) -> None:
        det = LoopDetector()
        for i in range(10):
            calls = [
                Call("web_search", {"query": f"question part {i}"}),
                Call("fetch_url", {"url": f"https://site{i}.dev/page"}),
            ]
            check = det.record(calls, files_read=[], files_modified=[])
            self.assertNotEqual(check.severity, "critical")

    def test_truly_identical_searches_still_trip_kill_switch(self) -> None:
        det = LoopDetector()
        last = None
        for _ in range(8):
            last = det.record(
                [Call("web_search", {"query": "same query every time"})],
                files_read=[],
                files_modified=[],
            )
            if last.severity == "critical":
                break
        self.assertEqual(last.severity, "critical")


if __name__ == "__main__":
    unittest.main()
