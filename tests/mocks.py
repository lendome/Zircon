from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

from zirconAgent.core.types import LLMResponse, StreamChunk, ToolCall, ModelProfile
from zirconAgent.llm.router import ModelRouter
from zirconAgent.core.config import RouterConfig


def make_profile(name="default", roles=None) -> ModelProfile:
    return ModelProfile(
        name=name,
        base_url="https://fake.test/v1",
        api_key="test-key",
        model="test-model",
        max_tokens=4096,
        context_window=32000,
        roles=roles or ["default"],
    )


def make_router(responses: list[LLMResponse] | None = None) -> ModelRouter:
    profile = make_profile()
    cfg = RouterConfig(
        profiles=[profile],
        role_priority={"default": ["default"], "planner": ["default"], "fast": ["default"]},
        rate_limit_delay=0,
        max_retries=1,
    )
    router = ModelRouter(cfg)
    if responses:
        router.generate = AsyncMock(side_effect=responses)
    else:
        router.generate = AsyncMock(return_value=LLMResponse(content="mock response"))
    return router


async def _stream_from_response(response: LLMResponse) -> AsyncIterator[StreamChunk]:
    if response.content:
        chunk_size = max(1, len(response.content) // 3)
        for i in range(0, len(response.content), chunk_size):
            yield StreamChunk(text=response.content[i:i + chunk_size])
    yield StreamChunk(
        done=True,
        usage=response.usage,
        tool_calls=response.tool_calls if response.tool_calls else None,
    )


def make_stream_router(responses: list[LLMResponse] | None = None) -> ModelRouter:
    profile = make_profile()
    cfg = RouterConfig(
        profiles=[profile],
        role_priority={"default": ["default"], "planner": ["default"], "fast": ["default"]},
        rate_limit_delay=0,
        max_retries=1,
    )
    router = ModelRouter(cfg)

    _responses = responses or [LLMResponse(content="mock response")]
    _idx = 0

    async def _mock_generate(**kwargs):
        nonlocal _idx
        resp = _responses[_idx] if _idx < len(_responses) else _responses[-1]
        _idx += 1
        return resp

    async def _mock_generate_stream(**kwargs):
        nonlocal _idx
        resp = _responses[_idx] if _idx < len(_responses) else _responses[-1]
        _idx += 1
        async for chunk in _stream_from_response(resp):
            yield chunk

    router.generate = _mock_generate
    router.generate_stream = _mock_generate_stream
    return router


def tool_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[])


def tool_call_response(calls: list[tuple[str, dict]], content: str = "") -> LLMResponse:
    tc_list = []
    for i, (name, args) in enumerate(calls):
        tc_list.append(ToolCall(id=f"call_{i}", name=name, arguments=args))
    return LLMResponse(content=content, tool_calls=tc_list)


class ResponseSequence:
    def __init__(self, *responses: LLMResponse):
        self.responses = list(responses)
        self.index = 0

    async def __call__(self, **kwargs):
        if self.index >= len(self.responses):
            return LLMResponse(content="no more responses")
        resp = self.responses[self.index]
        self.index += 1
        return resp
