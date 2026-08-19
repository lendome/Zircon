from zirconAgent.core.types import (
    Plan, PlanStep, AgentResult, LLMResponse, ToolCall,
    SubAgentResult, EditResult, TraceEvent, StreamChunk, ModelProfile,
)


class TestPlanStep:
    def test_from_dict_full(self):
        d = {"index": 0, "description": "read files", "action": "explore", "target_files": ["a.py"]}
        step = PlanStep.from_dict(d)
        assert step.index == 0
        assert step.description == "read files"
        assert step.action == "explore"
        assert step.target_files == ["a.py"]

    def test_from_dict_minimal(self):
        step = PlanStep.from_dict({"description": "do stuff"})
        assert step.index == 0
        assert step.action == "explore"
        assert step.target_files == []

    def test_from_dict_empty(self):
        step = PlanStep.from_dict({})
        assert step.description == ""


class TestPlan:
    def test_from_dict_full(self):
        d = {
            "steps": [
                {"index": 0, "description": "explore", "action": "explore"},
                {"index": 1, "description": "edit", "action": "edit"},
            ],
            "files_likely_needed": ["foo.py"],
            "complexity": "complex",
        }
        plan = Plan.from_dict(d)
        assert len(plan.steps) == 2
        assert plan.complexity == "complex"
        assert plan.files_likely_needed == ["foo.py"]

    def test_from_dict_defaults(self):
        plan = Plan.from_dict({"steps": []})
        assert plan.complexity == "moderate"
        assert plan.files_likely_needed == []
        assert plan.spirit is None

    def test_from_dict_spirit_extracted(self):
        d = {
            "steps": [{"index": 0, "description": "edit", "action": "edit"}],
            "spirit": {
                "literal_request": "make it faster",
                "underlying_intent": "fix the slow algorithm",
                "cheap_ways_out": ["caching around the bottleneck"],
            },
        }
        plan = Plan.from_dict(d)
        assert plan.spirit is not None
        assert plan.spirit["underlying_intent"] == "fix the slow algorithm"
        assert plan.spirit["cheap_ways_out"] == ["caching around the bottleneck"]

    def test_from_dict_spirit_non_dict_ignored(self):
        plan = Plan.from_dict({"steps": [], "spirit": "make it faster"})
        assert plan.spirit is None


class TestLLMResponse:
    def test_usage_properties(self):
        r = LLMResponse(content="hi", usage={"prompt_tokens": 100, "completion_tokens": 50})
        assert r.input_tokens == 100
        assert r.output_tokens == 50

    def test_empty_usage(self):
        r = LLMResponse(content="hi")
        assert r.input_tokens == 0
        assert r.output_tokens == 0

    def test_cost_from_usage(self):
        response = LLMResponse(content="hi", usage={"cost": "0.0125"})
        assert response.cost == 0.0125


class TestToolCall:
    def test_basic(self):
        tc = ToolCall(id="1", name="read_file", arguments={"path": "foo.py"})
        assert tc.name == "read_file"
        assert tc.arguments["path"] == "foo.py"


class TestSubAgentResult:
    def test_defaults(self):
        r = SubAgentResult(success=True, output="found it")
        assert r.files_read == []
        assert r.files_modified == []
        assert r.artifacts == {}


class TestEditResult:
    def test_success(self):
        r = EditResult(success=True, matcher="exact")
        assert r.error == ""

    def test_failure(self):
        r = EditResult(success=False, error="no match")
        assert r.matcher == ""


class TestTraceEvent:
    def test_basic(self):
        e = TraceEvent(phase="tool_call", detail="read_file", payload={"path": "x"})
        assert e.phase == "tool_call"


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult(success=True, answer="done")
        assert r.files_modified == []
        assert r.trace == []
        assert r.tokens_used == 0


class TestModelProfile:
    def test_basic(self):
        p = ModelProfile(name="test", base_url="http://x", api_key="k", model="m")
        assert p.max_tokens == 32768
        assert p.context_window == 128000
        assert p.roles == []
