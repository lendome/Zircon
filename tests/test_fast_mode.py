"""Fast mode: nitro/throughput routing in the model router."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.core.types import ModelProfile
from zirconAgent.core.config import RouterConfig
from zirconAgent.llm.router import ModelRouter


def _router(base_url: str) -> ModelRouter:
    profile = ModelProfile(
        name="default", base_url=base_url, api_key="k",
        model="deepseek/deepseek-v4-flash", roles=["default"],
    )
    return ModelRouter(RouterConfig(profiles=[profile]))


class TestFastMode(unittest.TestCase):
    def test_default_off(self) -> None:
        r = _router("https://openrouter.ai/api/v1")
        self.assertFalse(r.fast_mode)
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertNotIn("provider", payload)

    def test_enabled_adds_throughput_sort(self) -> None:
        r = _router("https://openrouter.ai/api/v1")
        r.set_fast_mode(True)
        self.assertTrue(r.fast_mode)
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertEqual(payload["provider"]["sort"], "throughput")

    def test_toggle_off_removes_it(self) -> None:
        r = _router("https://openrouter.ai/api/v1")
        r.set_fast_mode(True)
        r.set_fast_mode(False)
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertNotIn("provider", payload)

    def test_non_openrouter_backend_unaffected(self) -> None:
        # A direct (non-OpenRouter) endpoint has no throughput routing;
        # adding a provider field could break it, so it must be omitted.
        r = _router("https://api.anthropic.com/v1")
        r.set_fast_mode(True)
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertNotIn("provider", payload)

    def test_preserves_existing_tools(self) -> None:
        r = _router("https://openrouter.ai/api/v1")
        r.set_fast_mode(True)
        tools = [{"name": "read_file", "parameters": {}}]
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], tools, 100)
        self.assertIn("tools", payload)
        self.assertEqual(payload["provider"]["sort"], "throughput")


class TestReasoningEffort(unittest.TestCase):
    def _router_with(self, **profile_kw):
        profile = ModelProfile(
            name="default", base_url="https://openrouter.ai/api/v1", api_key="k",
            model="z-ai/glm-5.2", roles=["default"], **profile_kw,
        )
        return ModelRouter(RouterConfig(profiles=[profile]))

    def test_effort_passed_through(self) -> None:
        r = self._router_with(reasoning_effort="xhigh")
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertEqual(payload["reasoning"], {"effort": "xhigh"})

    def test_global_effort_is_fallback_when_profile_unset(self) -> None:
        # No per-profile effort -> the router-global reasoning_effort applies
        # (upstream default "medium").
        r = self._router_with()
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertEqual(payload["reasoning"], {"effort": "medium"})

    def test_profile_effort_overrides_global(self) -> None:
        r = self._router_with(reasoning_effort="xhigh")
        r.reasoning_effort = "low"  # global still set; profile must win
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertEqual(payload["reasoning"], {"effort": "xhigh"})

    def test_no_reasoning_when_global_none_and_profile_unset(self) -> None:
        r = self._router_with()
        r.reasoning_effort = "none"
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertNotIn("reasoning", payload)

    def test_enabled_false_wins_over_effort(self) -> None:
        r = self._router_with(reasoning_effort="xhigh", reasoning_enabled=False)
        payload = r._build_payload(r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100)
        self.assertEqual(payload["reasoning"], {"enabled": False})

    def test_disable_reasoning_flag_turns_off(self) -> None:
        r = self._router_with(reasoning_effort="xhigh")
        payload = r._build_payload(
            r._profiles["default"], [{"role": "user", "content": "hi"}], None, 100,
            disable_reasoning=True,
        )
        self.assertEqual(payload["reasoning"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
