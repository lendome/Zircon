import pytest
from unittest.mock import AsyncMock, patch
import httpx

from zirconAgent.core.types import LLMResponse, ToolCall, ModelProfile
from zirconAgent.core.config import RouterConfig
from zirconAgent.llm.router import ModelRouter
from zirconAgent.tests.mocks import make_profile


def make_router_with_profiles(*profiles: ModelProfile) -> ModelRouter:
    cfg = RouterConfig(
        profiles=list(profiles),
        role_priority={
            "default": [p.name for p in profiles],
            "planner": [p.name for p in profiles],
            "fast": [p.name for p in profiles],
        },
        rate_limit_delay=0,
        max_retries=2,
        retry_base_delay=0.01,
    )
    return ModelRouter(cfg)


class TestRoleSelection:
    def test_select_by_role_priority(self):
        p1 = make_profile("frontier", ["default", "frontier"])
        p2 = make_profile("default", ["default"])
        cfg = RouterConfig(
            profiles=[p1, p2],
            role_priority={"default": ["default", "frontier"], "frontier": ["frontier", "default"]},
            rate_limit_delay=0,
        )
        router = ModelRouter(cfg)
        candidates = router.select("frontier")
        assert candidates[0].name == "frontier"

    def test_select_default_fallback(self):
        p = make_profile("default", ["default"])
        router = make_router_with_profiles(p)
        candidates = router.select("nonexistent_role")
        assert len(candidates) >= 1
        assert candidates[0].name == "default"

    def test_select_no_profiles(self):
        cfg = RouterConfig(profiles=[], role_priority={}, rate_limit_delay=0)
        router = ModelRouter(cfg)
        candidates = router.select("anything")
        assert len(candidates) == 0


class TestContextWindow:
    def test_context_window_from_profile(self):
        p = make_profile("default")
        p.context_window = 64000
        router = make_router_with_profiles(p)
        assert router.context_window == 64000


class TestGenerate:
    @pytest.mark.asyncio
    async def test_generate_success(self):
        router = make_router_with_profiles(make_profile("default"))
        expected = LLMResponse(content="hello", tool_calls=[])
        router._call = AsyncMock(return_value=expected)

        result = await router.generate("default", [{"role": "user", "content": "hi"}])
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_generate_failover(self):
        p1 = make_profile("bad")
        p2 = make_profile("good")
        router = make_router_with_profiles(p1, p2)

        good_response = LLMResponse(content="from good", tool_calls=[])
        call_count = 0

        async def mock_call(profile, messages, tools, max_tokens, **kwargs):
            nonlocal call_count
            call_count += 1
            if profile.name == "bad":
                raise httpx.TimeoutException("timeout")
            return good_response

        router._call = mock_call
        result = await router.generate("default", [{"role": "user", "content": "hi"}])
        assert result.content == "from good"

    @pytest.mark.asyncio
    async def test_generate_all_fail(self):
        router = make_router_with_profiles(make_profile("bad"))
        router._call = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(RuntimeError, match="All models failed"):
            await router.generate("default", [{"role": "user", "content": "hi"}])

        assert router._call.await_count == 3

    @pytest.mark.asyncio
    async def test_generate_retries_connection_interrupt_after_three_seconds(self):
        router = make_router_with_profiles(make_profile("default"))
        router._call = AsyncMock(
            side_effect=[httpx.ConnectError("connection interrupted"), LLMResponse(content="recovered")]
        )

        with patch("zirconAgent.llm.router.asyncio.sleep", new_callable=AsyncMock) as sleep:
            result = await router.generate("default", [{"role": "user", "content": "hi"}])

        assert result.content == "recovered"
        sleep.assert_awaited_once_with(3.0)


class TestToolCallsParsing:
    def test_parse_tool_calls(self):
        raw = [
            {
                "id": "call_1",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "app.py"}',
                },
            }
        ]
        result = ModelRouter._parse_tool_calls(raw)
        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"path": "app.py"}

    def test_parse_empty_tool_calls(self):
        assert ModelRouter._parse_tool_calls([]) == []

    def test_parse_malformed_arguments(self):
        raw = [
            {
                "id": "call_1",
                "function": {"name": "test", "arguments": "not json"},
            }
        ]
        result = ModelRouter._parse_tool_calls(raw)
        assert result[0].arguments == {}

    def test_parse_arguments_markdown_fenced(self):
        args = ModelRouter._parse_arguments('```json\n{"path": "app.py"}\n```')
        assert args == {"path": "app.py"}

    def test_parse_arguments_double_encoded(self):
        args = ModelRouter._parse_arguments('"{\\"path\\": \\"app.py\\"}"')
        assert args == {"path": "app.py"}

    def test_parse_arguments_empty(self):
        assert ModelRouter._parse_arguments("") == {}
        assert ModelRouter._parse_arguments("   ") == {}


class TestBuildPayload:
    def test_requested_tokens_are_clamped_to_profile_limit(self):
        profile = make_profile("default")
        profile.max_tokens = 32768
        router = make_router_with_profiles(profile)

        payload = router._build_payload(profile, [], None, 64000)

        assert payload["max_tokens"] == 32768

    def test_basic_payload(self):
        router = make_router_with_profiles(make_profile("default"))
        profile = router._profiles["default"]
        payload = router._build_payload(
            profile,
            [{"role": "user", "content": "hi"}],
            None,
            None,
        )
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert "tools" not in payload

    def test_payload_with_tools(self):
        router = make_router_with_profiles(make_profile("default"))
        profile = router._profiles["default"]
        tools = [{"name": "read_file", "parameters": {}}]
        payload = router._build_payload(profile, [], tools, None)
        assert "tools" in payload
        assert payload["tools"][0]["type"] == "function"

    def test_nitro_appends_suffix_for_openrouter(self):
        profile = make_profile("default")
        profile.base_url = "https://openrouter.ai/api/v1"
        router = make_router_with_profiles(profile)
        router.set_nitro_mode(True)

        payload = router._build_payload(profile, [], None, None)

        assert payload["model"] == "test-model:nitro"
        assert profile.model == "test-model"

    def test_nitro_does_not_duplicate_suffix(self):
        profile = make_profile("default")
        profile.base_url = "https://openrouter.ai/api/v1"
        profile.model = "test-model:nitro"
        router = make_router_with_profiles(profile)
        router.set_nitro_mode(True)

        assert router._build_payload(profile, [], None, None)["model"] == "test-model:nitro"

    def test_nitro_does_not_modify_non_openrouter_models(self):
        profile = make_profile("default")
        profile.base_url = "https://api.example.com/v1"
        router = make_router_with_profiles(profile)
        router.set_nitro_mode(True)

        assert router._build_payload(profile, [], None, None)["model"] == "test-model"
