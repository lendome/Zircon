from __future__ import annotations

import pytest

from zirconAgent.core.loop_detector import LoopDetector, ToolFingerprint


class FakeCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


def make_call(name, **kwargs):
    return FakeCall(name, kwargs)


class TestToolFingerprint:
    def test_from_call_simple(self):
        fp = ToolFingerprint.from_call("read_file", {"path": "foo.py"})
        assert fp.name == "read_file"
        assert ("path", "foo.py") in fp.key_args

    def test_from_call_ignores_irrelevant(self):
        fp = ToolFingerprint.from_call("read_file", {"path": "foo.py", "content": "lots of text"})
        assert ("content", "lots of text") not in fp.key_args

    def test_equality(self):
        a = ToolFingerprint.from_call("read_file", {"path": "foo.py"})
        b = ToolFingerprint.from_call("read_file", {"path": "foo.py"})
        c = ToolFingerprint.from_call("read_file", {"path": "bar.py"})
        assert a == b
        assert a != c


class TestLoopDetector:
    def test_reverse_navigation_counts_as_read_only_exploration(self):
        det = LoopDetector(max_repetitions=99, read_only_warn_turns=2)
        calls = [
            make_call("get_callers", symbol="first"),
            make_call("get_ast_range", path="a.py", start_line=1, end_line=3),
            make_call("get_callers", symbol="second"),
        ]

        assert det.record([calls[0]], files_read=["a.py"], files_modified=[]).severity == "ok"
        det.record([calls[1]], files_read=["a.py"], files_modified=[])
        check = det.record([calls[2]], files_read=["b.py"], files_modified=[])

        assert check.severity == "warning"
        assert "read-only" in check.reason.lower()

    def test_ok_on_varied_calls(self):
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c1 = make_call("read_file", path="a.py")
        c2 = make_call("read_file", path="b.py")
        c3 = make_call("edit_file", path="a.py", search="x", replace="y")

        check = det.record([c1], files_read=["a.py"], files_modified=[])
        assert check.severity == "ok"

        check = det.record([c2], files_read=["b.py"], files_modified=[])
        assert check.severity == "ok"

        check = det.record([c3], files_read=[], files_modified=["a.py"])
        assert check.severity == "ok"

    def test_warning_on_repetition(self):
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c = make_call("read_file", path="a.py")

        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "ok"
        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "warning"
        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "warning"

    def test_stagnation_no_longer_critical(self):
        """Stagnation detection is removed — consecutive commands are productive work.
        With max_repetitions=2, repeated identical calls trigger warnings, but never critical."""
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c = make_call("grep_code", pattern="foo")

        assert det.record([c], files_read=[], files_modified=[]).severity == "ok"
        # Second identical call: near-repetition warning (repetition_count=1 >= max(1, max_rep-1)=1)
        assert det.record([c], files_read=[], files_modified=[]).severity == "warning"
        # Third identical call: warning again (never critical)
        assert det.record([c], files_read=[], files_modified=[]).severity == "warning"

    def test_progress_resets_stagnation(self):
        """Distinct calls never trigger warnings, even without reading/modifying files."""
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c1 = make_call("grep_code", pattern="foo")
        c2 = make_call("grep_code", pattern="bar")
        c3 = make_call("grep_code", pattern="baz")
        c4 = make_call("grep_code", pattern="qux")

        assert det.record([c1], files_read=[], files_modified=[]).severity == "ok"
        assert det.record([c2], files_read=[], files_modified=[]).severity == "ok"
        assert det.record([c3], files_read=[], files_modified=[]).severity == "ok"
        assert det.record([c4], files_read=[], files_modified=[]).severity == "ok"

    def test_progress_summary(self):
        det = LoopDetector()
        c1 = make_call("read_file", path="a.py")
        c2 = make_call("edit_file", path="b.py")
        det.record([c1], files_read=["a.py"], files_modified=[])
        det.record([c2], files_read=[], files_modified=["b.py"])
        summary = det.progress_summary()
        assert "a.py" in summary
        assert "b.py" in summary

    def test_reset(self):
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c = make_call("read_file", path="a.py")
        det.record([c], files_read=["a.py"], files_modified=[])
        det.record([c], files_read=["a.py"], files_modified=[])
        det.reset()
        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "ok"

    def test_different_calls_no_loop(self):
        det = LoopDetector(window_size=3, max_repetitions=2, stagnation_threshold=3)
        c1 = make_call("read_file", path="a.py")
        c2 = make_call("read_file", path="a.py", start=1, end=10)
        # Different line ranges are DISTINCT calls (not a loop), so both are ok.
        assert det.record([c1], files_read=["a.py"], files_modified=[]).severity == "ok"
        assert det.record([c2], files_read=["a.py"], files_modified=[]).severity == "ok"

    def test_defaults_allow_more_repetition_before_warning(self):
        """window=8, max_rep=3 means warning on 3rd identical repeat, always warning after."""
        det = LoopDetector()
        c = make_call("read_file", path="a.py")
        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "ok"
        assert det.record([c], files_read=["a.py"], files_modified=[]).severity == "ok"
        # 3rd identical: repetition_count=2 → >= max(1, max_rep-1)=2 → warning
        check = det.record([c], files_read=["a.py"], files_modified=[])
        assert check.severity == "warning", f"Expected warning on 3rd repeat, got {check.severity}"
        # 4th identical: repetition_count=3 → >= max_rep=3 → warning (never critical)
        check = det.record([c], files_read=["a.py"], files_modified=[])
        assert check.severity == "warning", f"Expected warning on 4th repeat, got {check.severity}"

    def test_stagnation_no_longer_detected(self):
        """Stagnation threshold no longer triggers warnings — consecutive varied commands are productive."""
        det = LoopDetector()
        # All varied calls with no new files each time — stagnation removed, all ok
        for pattern in ("a", "b", "c", "d", "e", "f"):
            c = make_call("grep_code", pattern=pattern)
            check = det.record([c], files_read=[], files_modified=[])
            assert check.severity == "ok", f"Expected ok for varied call (twp={check.turns_without_progress}), got {check.severity}"

    def test_different_line_ranges_never_critical(self):
        """Reading different line ranges of the same file is exploration, not a
        loop. Even many such reads must never escalate to critical (the scenario
        that previously produced a false 'incomplete' stop)."""
        det = LoopDetector()
        for start, end in [(1, 50), (51, 100), (101, 150), (160, 200),
                           (210, 260), (270, 320), (330, 380), (390, 440),
                           (450, 500), (510, 560), (570, 620), (630, 680)]:
            c = make_call("read_file", path="fn_applyBehavior.sqf", start=start, end=end)
            check = det.record([c], files_read=["fn_applyBehavior.sqf"], files_modified=[])
            assert check.severity != "critical", (
                f"read of lines {start}-{end} wrongly escalated to critical: {check.reason}"
            )

    def test_same_line_range_chunk_warning_never_critical(self):
        """The chunk-repeat detector itself never escalates to critical — it is a
        warning only. (A true infinite loop of IDENTICAL calls is caught by the
        separate exact-identical-turns detector; here we disable that by raising
        its threshold so we isolate the chunk detector.)"""
        det = LoopDetector(identical_turns_critical=10_000)
        c = make_call("read_file", path="a.py", start=10, end=20)
        for _ in range(12):
            check = det.record([c], files_read=["a.py"], files_modified=[])
            assert check.severity != "critical", f"chunk detector escalated: {check.reason}"

    def test_exact_identical_turns_eventually_critical(self):
        """The ONLY critical condition: the exact same tool-call set emitted
        repeatedly with zero variation (a genuine infinite loop)."""
        det = LoopDetector(identical_turns_critical=4)
        c = make_call("grep_code", pattern="foo")
        for i in range(4):
            check = det.record([c], files_read=[], files_modified=[])
            assert check.severity != "critical", f"premature critical at turn {i}: {check.reason}"
        # 5th identical turn (consecutive_identical_turns == 4) -> critical
        check = det.record([c], files_read=[], files_modified=[])
        assert check.severity == "critical"
        assert "exact same tool calls" in check.reason.lower()

    def test_edits_to_same_file_different_content_not_critical(self):
        """Regression: two edit_file calls to the same path but with different
        search/replace content must produce DISTINCT fingerprints. Previously
        the fingerprint only captured `path`, so every edit to the same file
        looked identical and triggered a false 'exact same tool calls' critical
        stop after identical_turns_critical consecutive edits."""
        det = LoopDetector(identical_turns_critical=4)
        edits = [
            make_call("edit_file", path="skyward.html", search=f"old_{i}", replace=f"new_{i}")
            for i in range(8)
        ]
        for i, c in enumerate(edits):
            check = det.record([c], files_read=[], files_modified=["skyward.html"])
            assert check.severity != "critical", (
                f"distinct edit #{i} wrongly escalated to critical: {check.reason}"
            )

    def test_identical_edits_to_same_file_do_trigger_critical(self):
        """Sanity: truly identical edits (same search/replace) to the same
        file must still be detected as a genuine loop."""
        det = LoopDetector(identical_turns_critical=3)
        c = make_call("edit_file", path="a.py", search="x", replace="y")
        for i in range(3):
            check = det.record([c], files_read=[], files_modified=["a.py"])
            assert check.severity != "critical", f"premature critical at turn {i}: {check.reason}"
        # 4th identical -> critical
        check = det.record([c], files_read=[], files_modified=["a.py"])
        assert check.severity == "critical"

    def test_create_file_different_content_not_critical(self):
        """create_file calls with different content to the same path are distinct."""
        det = LoopDetector(identical_turns_critical=3)
        for i in range(6):
            c = make_call("create_file", path="out.txt", content=f"version {i}")
            check = det.record([c], files_read=[], files_modified=["out.txt"])
            assert check.severity != "critical", f"create #{i} wrongly critical: {check.reason}"

    def test_identical_then_varied_resets_critical_counter(self):
        """A single varied turn must reset the exact-identical counter so a
        later identical burst doesn't carry over an old streak."""
        det = LoopDetector(identical_turns_critical=3)
        c = make_call("grep_code", pattern="foo")
        for _ in range(3):
            det.record([c], files_read=[], files_modified=[])
        # varied turn resets
        det.record([make_call("grep_code", pattern="bar")], files_read=[], files_modified=[])
        # now identical again — should need a fresh streak, not inherit the old one
        check = det.record([c], files_read=[], files_modified=[])
        assert check.severity != "critical", f"counter not reset: {check.reason}"
