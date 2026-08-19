from pathlib import Path

from zirconAgent.llm.router import ModelRouter, _normalize_usage


def test_normalize_usage_accepts_top_level_provider_cost() -> None:
    usage = _normalize_usage({"usage": {"prompt_tokens": 10}, "cost": 0.0042})

    assert usage["prompt_tokens"] == 10
    assert usage["cost"] == 0.0042


def test_router_accumulates_provider_costs() -> None:
    router = ModelRouter.__new__(ModelRouter)
    router.session_cost_usd = 0.0

    router._record_cost({"cost": 0.0125})
    router._record_cost({"cost": "0.0075"})

    assert router.session_cost_usd == 0.02
