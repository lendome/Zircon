"""Regression tests for the /models picker (_ModelPicker).

The profile stage was a dead end: ``selected_profile`` looked profiles up
by ``selected_profile_id``, which is only set *inside* ``select_profile()``,
so at the profile stage Enter was silently a no-op and no model could ever
be changed through the UI.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from cli.tui.components.chat import ChatComponent, _ModelPicker


class _Color:
    def to_rich(self) -> str:
        return ""


class _Theme:
    border_active = _Color()
    text_muted = _Color()
    info = _Color()
    primary = _Color()
    warning = _Color()
    success = _Color()


def _profiles(catalog: dict | None = None) -> list[dict]:
    return [
        {
            "id": "default",
            "model": "z-ai/glm-5.3",
            "base_url": "https://openrouter.ai/api/v1",
            "roles": ["default"],
            "available_models": list(catalog or []),
        },
        {
            "id": "chat",
            "model": "z-ai/glm-5.3",
            "base_url": "https://openrouter.ai/api/v1",
            "roles": ["chat"],
            "available_models": [],
        },
    ]


def _roles() -> list[str]:
    return ["default", "chat", "editor", "planner", "advisor"]


class TestSelectedProfile:
    def test_profile_stage_returns_indexed_option(self):
        picker = _ModelPicker(_roles(), _profiles(), _Theme())
        picker.select_role()  # role -> profile
        assert picker.stage == "profile"
        assert picker.selected_profile is picker.profiles[0]

    def test_profile_stage_second_row(self):
        picker = _ModelPicker(_roles(), _profiles(), _Theme())
        picker.select_role()
        picker.move(1)
        assert picker.selected_profile is picker.profiles[1]

    def test_catalog_stage_resolves_by_id(self):
        picker = _ModelPicker(_roles(), _profiles(["openai/gpt-4o"]), _Theme())
        picker.select_role()
        picker.select_profile()
        assert picker.stage == "catalog"
        assert picker.selected_profile is picker.profiles[0]


class TestFullFlow:
    def _component(self, catalog: dict) -> tuple[ChatComponent, AsyncMock]:
        comp = object.__new__(ChatComponent)
        transport = AsyncMock()
        transport.list_models.return_value = {
            "profiles": _profiles(catalog.get("default", [])),
            "roles": _roles(),
            "catalog": catalog,
        }
        transport.set_model.return_value = {"ok": True, "model": "openai/gpt-4o"}
        comp._transport = transport
        comp._toast_mgr = AsyncMock()
        comp._data = AsyncMock()
        comp._model_picker = None
        comp._update_footer = lambda: None
        comp._render = lambda: None
        return comp, transport

    def test_enter_advances_all_stages_and_assigns(self):
        comp, transport = self._component({"default": ["openai/gpt-4o", "z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=transport.list_models.return_value))
        picker = comp._model_picker
        assert picker.stage == "role"

        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        assert picker.stage == "profile"

        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        assert picker.stage == "catalog", "profile stage Enter was a no-op (regression)"

        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        transport.set_model.assert_awaited_once_with(
            picker.selected_role, "default", "openai/gpt-4o"
        )

    def test_backspace_from_profile_returns_to_roles(self):
        comp, transport = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=transport.list_models.return_value))
        picker = comp._model_picker
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "backspace"))
        assert picker.stage == "role"
