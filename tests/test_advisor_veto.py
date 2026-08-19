import pytest

from zirconAgent.core.executor import Executor
from zirconAgent.core.types import TierConfig, ToolCall
from zirconAgent.tools.registry import ToolRegistry
from zirconAgent.tests.mocks import make_router


def make_executor(**tier_overrides) -> Executor:
    tier = TierConfig(name="balanced", **tier_overrides)
    return Executor(make_router(), ToolRegistry(), tier_config=tier)


class TestParseVeto:
    def test_well_formed_block(self):
        feedback = 'Good progress.\n```veto\ntool: edit_file\nturns: 2\nreason: hand-placed timers\n```'
        cleaned, veto = Executor._parse_veto(feedback)
        assert veto == {"tool": "edit_file", "turns": 2, "reason": "hand-placed timers"}
        assert "```veto" not in cleaned
        assert "Good progress." in cleaned

    def test_no_block(self):
        cleaned, veto = Executor._parse_veto("just feedback")
        assert veto is None
        assert cleaned == "just feedback"

    def test_missing_tool_key(self):
        cleaned, veto = Executor._parse_veto('```veto\nturns: 2\n```')
        assert veto is None

    def test_bad_turns_defaults_to_two(self):
        _, veto = Executor._parse_veto('```veto\ntool: web_search\nturns: lots\n```')
        assert veto["turns"] == 2


class TestApplyVeto:
    def test_whitelisted_tool_gated(self):
        ex = make_executor()
        note = ex._apply_veto({"tool": "edit_file", "turns": 2, "reason": "timers"})
        assert note is not None
        assert "edit_file" in note
        assert ex._tool_gates["edit_file"] == [2, "timers"]

    def test_non_whitelisted_tool_rejected(self):
        ex = make_executor()
        assert ex._apply_veto({"tool": "read_file", "turns": 2, "reason": "x"}) is None
        assert ex._apply_veto({"tool": "run_command", "turns": 2, "reason": "x"}) is None
        assert ex._apply_veto({"tool": "get_function_body", "turns": 2, "reason": "x"}) is None
        assert ex._tool_gates == {}

    def test_turns_clamped_to_max(self):
        ex = make_executor(advisor_veto_max_turns=3)
        ex._apply_veto({"tool": "edit_file", "turns": 99, "reason": "x"})
        assert ex._tool_gates["edit_file"][0] == 3

    def test_disabled_flag_noop(self):
        ex = make_executor(advisor_veto_enabled=False)
        assert ex._apply_veto({"tool": "edit_file", "turns": 2, "reason": "x"}) is None
        assert ex._tool_gates == {}

    def test_no_stacking_on_active_gate(self):
        ex = make_executor()
        ex._tool_gates["edit_file"] = [2, "first"]
        assert ex._apply_veto({"tool": "edit_file", "turns": 3, "reason": "second"}) is None
        assert ex._tool_gates["edit_file"] == [2, "first"]

    def test_cooldown_blocks_re_veto(self):
        ex = make_executor()
        ex._veto_cooldown["edit_file"] = 3
        assert ex._apply_veto({"tool": "edit_file", "turns": 2, "reason": "x"}) is None
        assert "edit_file" not in ex._tool_gates


class TestToolGates:
    def _schemas(self, names):
        return [{"name": n, "description": "", "parameters": {}} for n in names]

    def test_gated_tool_stripped(self):
        ex = make_executor()
        ex._tool_gates["edit_file"] = [2, "reason"]
        out = ex._apply_tool_gates(self._schemas(["read_file", "edit_file", "run_command"]))
        names = [t["name"] for t in out]
        assert "edit_file" not in names
        assert "read_file" in names and "run_command" in names

    def test_gate_expires_after_n_turns(self):
        ex = make_executor()
        ex._tool_gates["web_search"] = [2, "thrash"]
        tools = self._schemas(["web_search", "fetch_url"])
        out1 = ex._apply_tool_gates(tools)  # turn 1: gated, 2->1
        assert "web_search" not in [t["name"] for t in out1]
        out2 = ex._apply_tool_gates(tools)  # turn 2: gated, 1->0
        assert "web_search" not in [t["name"] for t in out2]
        out3 = ex._apply_tool_gates(tools)  # turn 3: expired -> cooldown
        assert "web_search" in [t["name"] for t in out3]
        assert ex._veto_cooldown.get("web_search", 0) > 0

    def test_cooldown_ticks_down(self):
        ex = make_executor()
        ex._veto_cooldown["edit_file"] = 2
        tools = self._schemas(["edit_file"])
        ex._apply_tool_gates(tools)
        assert ex._veto_cooldown["edit_file"] == 1
        ex._apply_tool_gates(tools)
        assert "edit_file" not in ex._veto_cooldown

    def test_empty_gates_returns_input(self):
        ex = make_executor()
        tools = self._schemas(["read_file"])
        assert ex._apply_tool_gates(tools) is tools

    @pytest.mark.asyncio
    async def test_execute_batch_denies_gated_tool(self):
        ex = make_executor()
        ex._tool_gates["edit_file"] = [2, "timers"]
        calls = [ToolCall(id="1", name="edit_file", arguments={"path": "x.py", "search": "a", "replace": "b"})]
        results = await ex._execute_batch(calls)
        assert len(results) == 1
        assert "temporarily disabled by the supervisor" in results[0]
        assert "timers" in results[0]

    def test_denial_not_counted_as_infra_failure(self):
        ex = make_executor()
        denial = "Error: tool 'edit_file' is temporarily disabled by the supervisor (2 turn(s) left): timers."
        assert ex._record_tool_outcome("edit_file", denial) is None
        assert ex._consecutive_tool_failures.get("edit_file", 0) == 0

    def test_circuit_breaker_not_counted_as_infra_failure(self):
        ex = make_executor()
        msg = "CIRCUIT-BREAKER: This exact command already failed..."
        assert ex._record_tool_outcome("run_command", msg) is None
        assert ex._consecutive_tool_failures.get("run_command", 0) == 0


class TestSearchGateCompat:
    """The web-search anti-thrash gate now flows through _tool_gates."""

    def test_search_thrash_arms_gate(self):
        ex = make_executor()
        calls = [ToolCall(id=str(i), name="web_search", arguments={"query": f"q{i}"}) for i in range(5)]
        msg = ex._record_research_progress(calls)
        assert msg is not None
        assert "web_search" in ex._tool_gates
        assert ex._tool_gates["web_search"][0] == 2

    def test_search_gate_strips_web_search(self):
        ex = make_executor()
        ex._tool_gates["web_search"] = [2, "thrash"]
        tools = [{"name": "web_search"}, {"name": "fetch_url"}]
        out = ex._apply_tool_gates(tools)
        assert [t["name"] for t in out] == ["fetch_url"]
