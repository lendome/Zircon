from __future__ import annotations

import pytest

from zirconAgent.cli.daemon.transport import LocalTransport
from zirconAgent.core.agent import Agent
from zirconAgent.core.config import AgentConfig, RouterConfig
from zirconAgent.core.types import ModelProfile, Tier
from zirconAgent.core.types import TIER_PRESETS


@pytest.mark.asyncio
async def test_status_reports_current_context_fill(tmp_path):
    profile = ModelProfile(
        name="test",
        base_url="https://fake.test/v1",
        api_key="test",
        model="test-model",
        max_tokens=4096,
        context_window=32_000,
        roles=["default"],
    )
    agent = Agent(
        repo_path=tmp_path,
        router_config=RouterConfig(
            profiles=[profile],
            role_priority={"default": ["test"]},
        ),
        agent_config=AgentConfig(),
        tier=Tier.LOW,
    )
    agent.context.add_user_message("x" * 4_000)

    status = await LocalTransport(agent).get_status()

    assert status["context_max_tokens"] == 32_000
    assert status["context_used_tokens"] >= 1_000
    assert status["context_percent"] == pytest.approx(
        status["context_used_tokens"] * 100 / 32_000
    )


def test_tier_context_windows_are_explicit():
    assert TIER_PRESETS[Tier.LOW].context_window == 32_000
    assert TIER_PRESETS[Tier.BALANCED].context_window == 128_000
    assert TIER_PRESETS[Tier.QUALITY].context_window == 256_000


@pytest.mark.asyncio
async def test_tier_switch_updates_all_context_limits(tmp_path):
    profile = ModelProfile(
        name="test",
        base_url="https://fake.test/v1",
        api_key="test",
        model="test-model",
        context_window=32_000,
        roles=["default"],
    )
    agent = Agent(
        repo_path=tmp_path,
        router_config=RouterConfig(
            profiles=[profile],
            role_priority={"default": ["test"]},
        ),
        agent_config=AgentConfig(),
        tier=Tier.BALANCED,
    )
    transport = LocalTransport(agent)

    assert agent.context.context_window == 128_000
    assert (await transport.get_status())["context_max_tokens"] == 128_000

    result = await transport.set_tier("quality")
    status = await transport.get_status()

    assert result["context_window"] == 256_000
    assert agent.context.context_window == 256_000
    assert agent.context.max_tokens == 256_000 - agent.context.safety_margin
    assert agent.executor._ctx_guard.context_window == 256_000
    assert agent.executor._ctx_guard.soft_threshold == int(256_000 * 0.70)
    assert agent.executor._trajectory_pruner.context_window == 256_000
    assert status["context_max_tokens"] == 256_000
