from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from zirconAgent.core.types import (
    Tier, TierConfig, TIER_PRESETS, PlanDecision,
    Plan, PlanStep, TaskStatus,
)
from zirconAgent.core.config import AgentConfig
from zirconAgent.core.distiller import Distiller
from zirconAgent.core.context import ContextManager, estimate_tokens
from zirconAgent.core.planner import PlanGatekeeper
from zirconAgent.core.executor import Executor
from zirconAgent.core.edit_engine import EditEngine


class TestTierConfig:
    def test_01_low_tier_name(self):
        assert TIER_PRESETS[Tier.LOW].name == "low"

    def test_02_balanced_tier_name(self):
        assert TIER_PRESETS[Tier.BALANCED].name == "balanced"

    def test_03_quality_tier_name(self):
        assert TIER_PRESETS[Tier.QUALITY].name == "quality"

    def test_04_low_working_set_max_files(self):
        assert TIER_PRESETS[Tier.LOW].working_set_max_files == 8

    def test_05_balanced_working_set_max_files(self):
        assert TIER_PRESETS[Tier.BALANCED].working_set_max_files == 15

    def test_06_quality_working_set_max_files(self):
        assert TIER_PRESETS[Tier.QUALITY].working_set_max_files == 30

    def test_07_low_tokens_per_file(self):
        assert TIER_PRESETS[Tier.LOW].tokens_per_file == 300

    def test_08_quality_tokens_per_file(self):
        assert TIER_PRESETS[Tier.QUALITY].tokens_per_file == 1500

    def test_09_low_skip_final_verification(self):
        assert TIER_PRESETS[Tier.LOW].skip_final_verification is True

    def test_10_quality_reflection_enabled(self):
        assert TIER_PRESETS[Tier.QUALITY].reflection_loop is True


class TestContextManagerTierBehavior:
    def test_11_low_working_set_size(self):
        cfg = TIER_PRESETS[Tier.LOW]
        ctx = ContextManager(".", tier_config=cfg)
        assert ctx.working_set.max_size == 8

    def test_12_balanced_working_set_size(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        ctx = ContextManager(".", tier_config=cfg)
        assert ctx.working_set.max_size == 15

    def test_13_quality_working_set_size(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        ctx = ContextManager(".", tier_config=cfg)
        assert ctx.working_set.max_size == 30

    def test_14_low_file_truncation(self):
        cfg = TIER_PRESETS[Tier.LOW]
        ctx = ContextManager(".", tier_config=cfg)
        long_content = "x" * 5000
        ctx.add_file_to_working_set("test.py", long_content)
        stored = ctx.working_set["test.py"]
        assert len(stored) < 5000
        assert "truncated" in stored

    def test_15_quality_file_less_truncation(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        ctx = ContextManager(".", tier_config=cfg)
        long_content = "x" * 3000
        ctx.add_file_to_working_set("test.py", long_content)
        stored = ctx.working_set["test.py"]
        assert len(stored) >= 3000  # 1500 tokens * 4 = 6000 chars cap

    def test_16_modified_file_higher_cap(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        ctx = ContextManager(".", tier_config=cfg)
        ctx.mark_modified("test.py")
        long_content = "x" * 4000
        ctx.add_file_to_working_set("test.py", long_content)
        stored = ctx.working_set["test.py"]
        assert len(stored) < 4000  # modified gets 750 tokens = 3000 chars cap
        assert "truncated" in stored

    def test_17_low_history_keep_exchanges(self):
        cfg = TIER_PRESETS[Tier.LOW]
        ctx = ContextManager(".", tier_config=cfg)
        for i in range(20):
            ctx.add_user_message(f"msg {i}")
        recent = ctx._get_recent_history(100000)
        assert len(recent) <= 8  # 4 exchanges * 2

    def test_18_quality_history_keep_exchanges(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        ctx = ContextManager(".", tier_config=cfg)
        for i in range(40):
            ctx.add_user_message(f"msg {i}")
        recent = ctx._get_recent_history(100000)
        assert len(recent) <= 32  # 16 exchanges * 2

    def test_19_low_episodic_disabled(self):
        cfg = TIER_PRESETS[Tier.LOW]
        ctx = ContextManager(".", tier_config=cfg)
        assert cfg.episodic_memory_count == 0

    def test_20_quality_episodic_enabled(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        ctx = ContextManager(".", tier_config=cfg)
        assert cfg.episodic_memory_count == 50

    def test_21_low_repo_map_max_files(self):
        assert TIER_PRESETS[Tier.LOW].repo_map_max_files == 30

    def test_22_quality_repo_map_max_files(self):
        assert TIER_PRESETS[Tier.QUALITY].repo_map_max_files == 120

    def test_23_low_kg_disabled(self):
        assert TIER_PRESETS[Tier.LOW].kg_context_nodes == 0

    def test_24_quality_kg_enabled(self):
        assert TIER_PRESETS[Tier.QUALITY].kg_context_nodes == 20

    def test_25_low_semantic_disabled(self):
        assert TIER_PRESETS[Tier.LOW].semantic_search_enabled is False


class TestDistillerTierBehavior:
    def test_26_ultra_read_file_signal(self):
        cfg = TIER_PRESETS[Tier.LOW]
        d = Distiller(cfg)
        result = d.distill_for_history("line1\nline2\nline3", "read_file")
        assert "lines=" in result or "truncated" in result or "read_file" in result

    def test_27_ultra_grep_signal(self):
        cfg = TIER_PRESETS[Tier.LOW]
        d = Distiller(cfg)
        result = d.distill_for_history("a.py:1: foo\nb.py:2: bar", "grep_code")
        assert "matches" in result.lower() or result == ""

    def test_28_ultra_edit_signal(self):
        cfg = TIER_PRESETS[Tier.LOW]
        d = Distiller(cfg)
        result = d.distill_for_history("Applied exact to test.py", "edit_file")
        assert "edit" in result.lower() or "applied" in result.lower()

    def test_29_ultra_run_command_signal(self):
        cfg = TIER_PRESETS[Tier.LOW]
        d = Distiller(cfg)
        result = d.distill_for_history("Exit code: 0\nOutput: hello", "run_command")
        assert "exit" in result.lower()

    def test_30_tiered_read_file_keeps_some_content(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        d = Distiller(cfg)
        result = d.distill_for_history("line1\nline2\nline3\nline4\nline5", "read_file")
        assert "lines total" in result

    def test_31_tiered_grep_keeps_matches(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        d = Distiller(cfg)
        result = d.distill_for_history("a.py:1: foo\nb.py:2: bar", "grep_code")
        assert "a.py" in result or "matches" in result

    def test_32_tiered_list_dir_truncates(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        d = Distiller(cfg)
        items = "\n".join(f"file{i}.py" for i in range(60))
        result = d.distill_for_history(items, "glob_files")
        assert "total items" in result.lower() or len(result) < len(items)

    def test_33_gradual_preserves_more(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        d = Distiller(cfg)
        content = "line" + "\nline" * 50
        result = d.distill_for_history(content, "read_file")
        assert len(result) > 50

    def test_34_gradual_grep_preserves_matches(self):
        cfg = TIER_PRESETS[Tier.QUALITY]
        d = Distiller(cfg)
        matches = "\n".join(f"file{i}.py:{i}: match" for i in range(25))
        result = d.distill_for_history(matches, "grep_code")
        assert "file" in result

    def test_35_distill_to_signal_short_data_unchanged(self):
        d = Distiller()
        result = d.distill_to_signal("short")
        assert result == "short"

    def test_36_distill_to_signal_long_data_truncated(self):
        d = Distiller()
        result = d.distill_to_signal("a\nb\nc\nd\ne\nf")
        assert len(result) <= 200

    def test_37_generic_distill_under_limit(self):
        d = Distiller()
        result = d._generic_distill("hello", 100)
        assert result == "hello"

    def test_38_generic_distill_over_limit(self):
        d = Distiller()
        result = d._generic_distill("x" * 5000, 100)
        assert "truncated" in result

    def test_39_pytest_distill_extracts_failures(self):
        d = Distiller()
        output = "test_a.py::test_x PASSED\ntest_a.py::test_y FAILED\nE   assert 1 == 2\n=== 1 failed, 1 passed ==="
        result = d._distill_pytest(output, 500)
        assert "failed" in result.lower()

    def test_40_shell_distill_extracts_exit_code(self):
        d = Distiller()
        output = "Running...\nDone\nExit code: 1\nSTDERR: error"
        result = d._distill_shell(output, 500)
        assert "1" in result


class TestGatekeeperRules:
    @pytest.fixture
    def low_gatekeeper(self):
        from unittest.mock import MagicMock
        router = MagicMock()
        return PlanGatekeeper(router, TIER_PRESETS[Tier.LOW])

    @pytest.fixture
    def hybrid_gatekeeper(self):
        from unittest.mock import MagicMock
        router = MagicMock()
        return PlanGatekeeper(router, TIER_PRESETS[Tier.BALANCED])

    def test_41_greeting_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("hello")
        assert result is not None and result.needs_plan is False

    def test_42_hi_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("hi")
        assert result is not None and result.needs_plan is False

    def test_43_status_command_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("/status")
        assert result is not None and result.needs_plan is False

    def test_44_informational_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("what does this do?")
        assert result is not None and result.needs_plan is False

    def test_45_edit_keyword_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("fix the bug")
        assert result is None

    def test_46_create_keyword_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("create a new file")
        assert result is None

    def test_47_refactor_keyword_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("refactor the code")
        assert result is None

    def test_48_implement_keyword_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("implement login")
        assert result is None

    def test_49_short_query_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("test")
        assert result is not None and result.needs_plan is False

    def test_50_empty_input_no_plan(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("")
        assert result is not None and result.needs_plan is False

    def test_51_hybrid_greeting_bypasses_llm(self, hybrid_gatekeeper):
        result = hybrid_gatekeeper._rule_decide("hello there")
        assert result is not None and result.needs_plan is False

    def test_52_hybrid_edit_substantive_needs_plan(self, hybrid_gatekeeper):
        result = hybrid_gatekeeper._rule_decide("edit the function to handle errors")
        assert result is not None and result.needs_plan is True

    def test_53_hybrid_what_is_bypasses(self, hybrid_gatekeeper):
        result = hybrid_gatekeeper._rule_decide("what is the capital of France?")
        assert result is not None and result.needs_plan is False

    @pytest.mark.asyncio
    async def test_54_llm_only_mode_uses_llm(self):
        from unittest.mock import MagicMock, AsyncMock
        router = MagicMock()
        router.generate = AsyncMock(return_value=MagicMock(content="PLAN_REQUIRED: complex"))
        gk = PlanGatekeeper(router, TIER_PRESETS[Tier.QUALITY])
        result = await gk.decide("hello")
        assert result.needs_plan is True

    def test_55_rule_decide_improve_keyword(self, low_gatekeeper):
        result = low_gatekeeper._rule_decide("improve performance")
        assert result is None


class TestExecutorAndEditEngine:
    def test_56_low_max_tool_turns(self):
        assert TIER_PRESETS[Tier.LOW].max_tool_turns == 8

    def test_57_quality_max_tool_turns(self):
        assert TIER_PRESETS[Tier.QUALITY].max_tool_turns == 30

    def test_58_low_edit_repair_attempts(self):
        assert TIER_PRESETS[Tier.LOW].edit_repair_attempts == 1

    def test_59_quality_edit_repair_attempts(self):
        assert TIER_PRESETS[Tier.QUALITY].edit_repair_attempts == 5

    def test_60_low_skip_synthesis(self):
        assert TIER_PRESETS[Tier.LOW].skip_synthesis is True

    def test_61_balanced_synthesis_enabled(self):
        assert TIER_PRESETS[Tier.BALANCED].skip_synthesis is False

    def test_62_engine_exact_match(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n")
        result = engine.apply_search_replace(f, "def foo():\n    pass", "def bar():\n    pass")
        assert result.success
        assert "bar" in f.read_text()

    def test_63_engine_fuzzy_match(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    x = 1\n    return x\n")
        result = engine.apply_search_replace(f, "def foo():\n    x = 1\n    return x", "def foo():\n    x = 2\n    return x")
        assert result.success

    def test_64_engine_syntax_verification(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n")
        result = engine.apply_search_replace(f, "def foo():", "def foo(:")
        assert not result.success

    def test_65_engine_line_edit(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        result = engine.apply_line_edit(f, 2, 2, "replaced")
        assert result.success
        assert "replaced" in f.read_text()

    def test_66_engine_ast_replace(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n")
        result = engine.apply_ast_replace(f, "foo", "def foo():\n    return 42\n")
        assert result.success
        assert "return 42" in f.read_text()

    def test_67_engine_aider_blocks(self, tmp_path):
        engine = EditEngine()
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n")
        text = f"{tmp_path / 'test.py'}\n<<<<<<< SEARCH\ndef foo():\n    pass\n=======\ndef bar():\n    pass\n>>>>>>> REPLACE"
        results = engine.apply_aider_blocks(text, tmp_path)
        assert len(results) == 1
        assert results[0].success

    def test_68_engine_tier_repair_low(self, tmp_path):
        cfg = TIER_PRESETS[Tier.LOW]
        engine = EditEngine(cfg)
        assert engine.max_repair_attempts == 1

    def test_69_engine_tier_repair_quality(self, tmp_path):
        cfg = TIER_PRESETS[Tier.QUALITY]
        engine = EditEngine(cfg)
        assert engine.max_repair_attempts == 5

    def test_70_executor_history_turns(self):
        from unittest.mock import MagicMock, AsyncMock
        router = MagicMock()
        registry = MagicMock()
        router.generate = AsyncMock(return_value=MagicMock(
            content="done", tool_calls=[], usage={}
        ))
        ex = Executor(router, registry, TIER_PRESETS[Tier.LOW])
        assert hasattr(ex, "last_history_turns")


class TestAgentIntegration:
    @pytest.fixture
    def mock_agent_low(self, tmp_path):
        from unittest.mock import MagicMock, AsyncMock
        router_cfg = MagicMock()
        router_cfg.profiles = []
        router_cfg.role_priority = {}
        router_cfg.context_window = 32000
        agent_cfg = AgentConfig(tier=Tier.LOW)
        from zirconAgent.core.agent import Agent
        agent = Agent(repo_path=tmp_path, router_config=router_cfg, agent_config=agent_cfg)
        agent.router.generate = AsyncMock(return_value=MagicMock(
            content="DIRECT_OK: simple", tool_calls=[], usage={}
        ))
        return agent

    @pytest.fixture
    def mock_agent_balanced(self, tmp_path):
        from unittest.mock import MagicMock, AsyncMock
        router_cfg = MagicMock()
        router_cfg.profiles = []
        router_cfg.role_priority = {}
        router_cfg.context_window = 32000
        agent_cfg = AgentConfig(tier=Tier.BALANCED)
        from zirconAgent.core.agent import Agent
        agent = Agent(repo_path=tmp_path, router_config=router_cfg, agent_config=agent_cfg)
        agent.router.generate = AsyncMock(return_value=MagicMock(
            content="DIRECT_OK: simple", tool_calls=[], usage={}
        ))
        return agent

    def test_71_agent_tier_low(self, mock_agent_low):
        assert mock_agent_low.tier == Tier.LOW

    def test_72_agent_tier_balanced(self, mock_agent_balanced):
        assert mock_agent_balanced.tier == Tier.BALANCED

    def test_73_agent_system_prompt_low(self, mock_agent_low):
        prompt = mock_agent_low._get_system_prompt()
        assert "coding assistant with file tools" in prompt

    def test_74_agent_system_prompt_balanced(self, mock_agent_balanced):
        prompt = mock_agent_balanced._get_system_prompt()
        assert "expert coding assistant" in prompt

    def test_75_agent_system_prompt_quality(self, tmp_path):
        from unittest.mock import MagicMock
        router_cfg = MagicMock()
        router_cfg.profiles = []
        router_cfg.role_priority = {}
        router_cfg.context_window = 32000
        agent_cfg = AgentConfig(tier=Tier.QUALITY)
        from zirconAgent.core.agent import Agent
        agent = Agent(repo_path=tmp_path, router_config=router_cfg, agent_config=agent_cfg)
        prompt = agent._get_system_prompt()
        assert "elite software engineering assistant" in prompt

    def test_76_agent_tools_registered(self, mock_agent_low):
        names = mock_agent_low.registry.list_names()
        assert "read_file" in names
        assert "edit_file" in names
        assert "grep_code" in names

    def test_77_agent_context_has_tier(self, mock_agent_low):
        assert mock_agent_low.context.tier.name == "low"

    def test_78_agent_planner_has_tier(self, mock_agent_low):
        assert mock_agent_low.planner.tier.name == "low"

    def test_79_agent_executor_has_tier(self, mock_agent_low):
        assert mock_agent_low.executor.tier.name == "low"

    def test_80_agent_gatekeeper_mode_low(self, mock_agent_low):
        assert mock_agent_low._gatekeeper.tier.gatekeeper_mode == "rule_only"

    def test_81_agent_gatekeeper_mode_quality(self, tmp_path):
        from unittest.mock import MagicMock
        router_cfg = MagicMock()
        router_cfg.profiles = []
        router_cfg.role_priority = {}
        router_cfg.context_window = 32000
        agent_cfg = AgentConfig(tier=Tier.QUALITY)
        from zirconAgent.core.agent import Agent
        agent = Agent(repo_path=tmp_path, router_config=router_cfg, agent_config=agent_cfg)
        assert agent._gatekeeper.tier.gatekeeper_mode == "llm_only"

    def test_82_agent_get_tools_for_explore(self, mock_agent_low):
        step = PlanStep(index=0, description="explore", action="explore")
        tools = mock_agent_low._get_tools_for_step(step)
        assert len(tools) == 6

    def test_83_agent_get_tools_for_edit(self, mock_agent_low):
        step = PlanStep(index=0, description="edit", action="edit")
        tools = mock_agent_low._get_tools_for_step(step)
        assert len(tools) >= 5, f"Expected at least 5 tools for edit step, got {len(tools)}"
        names = [t["name"] for t in tools]
        assert "edit_file" in names or "edit_lines" in names or "create_file" in names

    def test_84_agent_read_file_content(self, mock_agent_low, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = mock_agent_low._read_file_content("test.txt")
        assert "hello" in result

    def test_85_agent_format_plan_text(self, mock_agent_low):
        plan = Plan(steps=[PlanStep(index=0, description="test", action="edit", target_files=["a.py"])])
        text = mock_agent_low._format_plan_text(plan)
        assert "a.py" in text


class TestTokenSavings:
    def test_86_low_vs_quality_working_set_tokens(self):
        low = TIER_PRESETS[Tier.LOW]
        quality = TIER_PRESETS[Tier.QUALITY]
        low_max = low.working_set_max_files * low.tokens_per_file
        quality_max = quality.working_set_max_files * quality.tokens_per_file
        assert low_max < quality_max
        assert low_max == 2400
        assert quality_max == 45000

    def test_87_history_distill_ultra_saves_tokens(self):
        cfg = TIER_PRESETS[Tier.LOW]
        d = Distiller(cfg)
        raw = "line\n" * 100
        distilled = d.distill_for_history(raw, "read_file")
        assert estimate_tokens(distilled) < estimate_tokens(raw)

    def test_88_history_distill_tiered_saves_tokens(self):
        cfg = TIER_PRESETS[Tier.BALANCED]
        d = Distiller(cfg)
        raw = "line\n" * 100
        distilled = d.distill_for_history(raw, "read_file")
        assert estimate_tokens(distilled) < estimate_tokens(raw)

    def test_89_low_skips_planning_saves_call(self):
        assert TIER_PRESETS[Tier.LOW].skip_planner is True

    def test_90_low_gatekeeper_no_llm_saves_call(self):
        assert TIER_PRESETS[Tier.LOW].gatekeeper_mode == "rule_only"

    def test_91_low_skip_synthesis_saves_call(self):
        assert TIER_PRESETS[Tier.LOW].skip_synthesis is True

    def test_92_low_skip_verification_saves_call(self):
        assert TIER_PRESETS[Tier.LOW].skip_final_verification is True

    def test_93_balanced_max_tokens_lower_than_quality(self):
        assert TIER_PRESETS[Tier.BALANCED].default_max_tokens < TIER_PRESETS[Tier.QUALITY].default_max_tokens

    def test_94_low_max_tokens_lowest(self):
        assert TIER_PRESETS[Tier.LOW].default_max_tokens < TIER_PRESETS[Tier.BALANCED].default_max_tokens

    def test_95_context_estimate_tokens(self):
        assert estimate_tokens("hello world") == 2
        assert estimate_tokens("") == 1


class TestSandbox:
    @pytest.fixture
    def sandbox_path(self):
        return Path(__file__).parent.parent / "sandbox"

    def test_96_sandbox_exists(self, sandbox_path):
        assert sandbox_path.exists()

    def test_97_sandbox_has_src(self, sandbox_path):
        assert (sandbox_path / "src" / "ecommerce" / "models" / "product.py").exists()

    def test_98_sandbox_has_tests(self, sandbox_path):
        assert (sandbox_path / "tests" / "test_product.py").exists()

    def test_99_sandbox_has_known_bugs(self, sandbox_path):
        content = (sandbox_path / "src" / "ecommerce" / "services" / "cart_service.py").read_text()
        assert "quntity" in content

    def test_100_sandbox_product_discount_bug(self, sandbox_path):
        content = (sandbox_path / "src" / "ecommerce" / "models" / "product.py").read_text()
        assert "apply_discount" in content