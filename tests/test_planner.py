import pytest
from unittest.mock import AsyncMock

from zirconAgent.core.planner import Planner, PlanGatekeeper
from zirconAgent.core.types import Plan, PlanStep, LLMResponse, PlanDecision, TaskStatus
from zirconAgent.tests.mocks import make_router, tool_response


@pytest.fixture
def planner():
    return Planner(make_router())


@pytest.fixture
def gatekeeper():
    return PlanGatekeeper(make_router())


class TestPlanGatekeeper:
    @pytest.mark.asyncio
    async def test_plan_required(self, gatekeeper):
        gatekeeper.router.generate = AsyncMock(
            return_value=LLMResponse(content="PLAN_REQUIRED: modifies multiple files")
        )
        decision = await gatekeeper.decide("refactor the auth module")
        assert decision.needs_plan is True
        assert "multiple files" in decision.reason

    @pytest.mark.asyncio
    async def test_direct_ok(self, gatekeeper):
        gatekeeper.router.generate = AsyncMock(
            return_value=LLMResponse(content="DIRECT_OK: single file informational")
        )
        decision = await gatekeeper.decide("what does foo.py do?")
        assert decision.needs_plan is False
        assert "No edit keywords" in decision.reason or "informational" in decision.reason

    @pytest.mark.asyncio
    async def test_fallback_edit_keywords(self, gatekeeper):
        gatekeeper.router.generate = AsyncMock(
            return_value=LLMResponse(content="unclear response")
        )
        decision = await gatekeeper.decide("fix the bug in utils.py")
        assert decision.needs_plan is True
        assert "edit" in decision.reason.lower() or "change" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_fallback_no_keywords(self, gatekeeper):
        gatekeeper.router.generate = AsyncMock(
            return_value=LLMResponse(content="unclear response")
        )
        decision = await gatekeeper.decide("hello there")
        assert decision.needs_plan is False


class TestPlanParsing:
    @pytest.mark.asyncio
    async def test_valid_json_plan(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(
                content='{"steps": [{"index": 0, "description": "read files", "action": "explore"}, {"index": 1, "description": "edit code", "action": "edit"}], "complexity": "moderate"}',
            )
        )
        plan = await planner.plan("fix the bug")
        assert len(plan.steps) == 2
        assert plan.steps[0].action == "explore"
        assert plan.steps[1].action == "edit"
        assert plan.complexity == "moderate"

    @pytest.mark.asyncio
    async def test_plan_with_target_files(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(
                content='{"steps": [{"index": 0, "description": "edit app.py", "action": "edit", "target_files": ["src/app.py"]}], "files_likely_needed": ["src/app.py"]}',
            )
        )
        plan = await planner.plan("add a docstring")
        assert plan.steps[0].target_files == ["src/app.py"]
        assert plan.files_likely_needed == ["src/app.py"]

    @pytest.mark.asyncio
    async def test_plan_with_markdown_json(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(
                content='```json\n{"steps": [{"index": 0, "description": "explore", "action": "explore"}]}\n```',
            )
        )
        plan = await planner.plan("look at code")
        assert len(plan.steps) == 1

    @pytest.mark.asyncio
    async def test_plan_with_surrounding_text(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(
                content='Here is the plan:\n{"steps": [{"index": 0, "description": "do it", "action": "edit"}]}\nLet me know if you need changes.',
            )
        )
        plan = await planner.plan("do something")
        assert len(plan.steps) == 1

    @pytest.mark.asyncio
    async def test_fallback_plan_on_invalid_json(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(content="I can't parse this into JSON."),
        )
        plan = await planner.plan("fix bug")
        assert len(plan.steps) >= 2
        assert plan.steps[0].action == "explore"
        assert any(s.action == "edit" for s in plan.steps)

    @pytest.mark.asyncio
    async def test_fallback_plan_on_empty_response(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(content=""),
        )
        plan = await planner.plan("task")
        assert len(plan.steps) >= 2


class TestReplan:
    @pytest.mark.asyncio
    async def test_replan_after_failure(self, planner):
        planner.router.generate = AsyncMock(
            return_value=LLMResponse(
                content='{"steps": [{"index": 0, "description": "re-explore", "action": "explore"}, {"index": 1, "description": "try again", "action": "edit"}], "complexity": "complex"}',
            )
        )
        failed = PlanStep(index=1, description="edit failed", action="edit")
        old_plan = Plan(steps=[PlanStep(index=0, description="explore", action="explore"), failed])
        new_plan = await planner.replan("fix bug", "context here", old_plan, failed)
        assert len(new_plan.steps) == 2
        assert "re-explore" in new_plan.steps[0].description
