from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from zirconAgent.cli.tui.components.chat import ChatComponent


def test_wheel_does_not_start_or_change_prompt_selection():
    chat = ChatComponent.__new__(ChatComponent)
    chat._mouse_selection_anchor = None
    chat._mouse_tracking_enabled = True

    with patch("zirconAgent.cli.tui.components.chat.disable_mouse_tracking") as disable:
        chat._handle_mouse("mouse:wheel_up:0:20:10")

    assert chat._mouse_selection_anchor is None
    assert not chat._mouse_tracking_enabled
    disable.assert_called_once_with()


def test_keyboard_reenables_mouse_tracking_after_native_scroll():
    chat = ChatComponent.__new__(ChatComponent)
    chat._mouse_tracking_enabled = False
    chat._pending_approval = None
    chat._last_ctrl_c_time = 0.0
    chat._double_ctrl_c_threshold = 2.0
    chat._is_streaming = SimpleNamespace(get=lambda: False)
    chat._palette = SimpleNamespace(is_visible=False)
    chat._model_picker = None
    chat._session_picker = None
    chat._reasoning_picker = None
    chat._checkpoint_picker = None
    chat._autocomplete = SimpleNamespace(is_visible=False, hide=lambda: None)
    chat._which_key = SimpleNamespace(is_visible=False)
    chat._keymap = SimpleNamespace(get_key_sequences=lambda name: [], dispatch_key=lambda key: False)
    chat._input = SimpleNamespace(text="", cursor=0, insert=lambda key: None)
    chat._try_input_action = lambda key: False
    async def check_autocomplete():
        return None

    chat._check_autocomplete = check_autocomplete
    chat._render = lambda: None

    with patch("zirconAgent.cli.tui.components.chat.enable_mouse_tracking") as enable:
        import asyncio
        asyncio.run(chat._handle_key("x"))

    assert chat._mouse_tracking_enabled
    enable.assert_called_once_with()
