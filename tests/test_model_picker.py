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
    return ["default", "chat", "editor", "planner", "architect", "advisor"]


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


class TestTypeToSearch:
    """Typing filters the visible list at every stage (no arrowing required)."""

    def _component(self, catalog: dict) -> tuple[ChatComponent, AsyncMock]:
        comp, transport = TestFullFlow._component(self, catalog)
        return comp, transport

    def test_typing_filters_role_list(self):
        comp, _ = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=comp._transport.list_models.return_value))
        picker = comp._model_picker
        for ch in "arch":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        assert picker.options == ["architect"], picker.options
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        assert picker.selected_role == "architect"
        assert picker.stage == "profile"

    def test_typing_filters_profiles_by_model_and_id(self):
        comp, _ = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=comp._transport.list_models.return_value))
        picker = comp._model_picker
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))  # role -> profile
        for ch in "cha":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        assert picker.options == [picker.profiles[1]], picker.options
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        assert picker.selected_profile_id == "chat"
        assert picker.stage == "catalog"

    def test_typing_filters_catalog_and_enter_assigns_match(self):
        comp, transport = self._component({"default": ["openai/gpt-4o", "z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=transport.list_models.return_value))
        picker = comp._model_picker
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        for ch in "gpt":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        assert picker.options == ["openai/gpt-4o"], picker.options
        assert picker.selected_model == "openai/gpt-4o"
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        transport.set_model.assert_awaited_once_with("default", "default", "openai/gpt-4o")

    def test_no_match_typed_text_is_custom_id(self):
        """Filtering takes precedence; zero matches means the query IS the model ID."""
        comp, transport = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=transport.list_models.return_value))
        picker = comp._model_picker
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        for ch in "o/l/m-4":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        assert picker.options == []
        assert picker.selected_model == "o/l/m-4"
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        transport.set_model.assert_awaited_once_with("default", "default", "o/l/m-4")

    def test_backspace_edits_query_before_navigating_back(self):
        comp, _ = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=comp._transport.list_models.return_value))
        picker = comp._model_picker
        for ch in "zz":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "backspace"))
        assert picker._text_buf == "z"
        assert picker.stage == "role", "backspace must edit the query, not exit the stage"
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "backspace"))
        assert picker._text_buf == ""
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "backspace"))
        # query empty now: but we are at role stage (top), so stay put
        assert picker.stage == "role"

    def test_enter_with_no_matches_does_not_crash(self):
        comp, _ = self._component({"default": ["z-ai/glm-5.3"]})
        asyncio.run(ChatComponent._show_model_picker(comp, _Theme(), data=comp._transport.list_models.return_value))
        picker = comp._model_picker
        for ch in "zzz":
            asyncio.run(ChatComponent._handle_model_picker_key(comp, ch))
        assert picker.options == []
        asyncio.run(ChatComponent._handle_model_picker_key(comp, "return"))
        assert picker.stage == "role", "no crash, stage unchanged"
