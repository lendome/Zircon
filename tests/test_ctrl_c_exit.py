"""Tests for Ctrl+C handling: double-press exits, single press interrupts."""

from __future__ import annotations

import asyncio
import io
import unittest
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
import sys
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.cli.tui.components.chat import ChatComponent
from zirconAgent.cli.tui.prompt.footer import PromptFooter


class FakeSignal:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeToast:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(msg)


def make_chat(streaming: bool = False) -> ChatComponent:
    chat = ChatComponent.__new__(ChatComponent)
    chat._pending_approval = None
    chat._last_ctrl_c_time = 0.0
    chat._double_ctrl_c_threshold = 2.0
    chat._last_escape_time = 0.0
    chat._double_escape_threshold = 0.4
    chat._is_streaming = FakeSignal(streaming)
    chat._streaming_task = None
    chat._streaming_cancelled = False
    chat._toast_mgr = FakeToast()
    chat._running = True
    chat._render_lines = 0
    chat._footer = PromptFooter()
    chat.console = Console(file=io.StringIO())
    # Stubs for the fall-through path of _handle_key
    chat._palette = SimpleNamespace(is_visible=False)
    chat._model_picker = None
    chat._session_picker = None
    chat._reasoning_picker = None
    chat._checkpoint_picker = None
    chat._autocomplete = SimpleNamespace(is_visible=False, hide=lambda: None)
    chat._which_key = SimpleNamespace(is_visible=False)
    chat._keymap = SimpleNamespace(
        get_key_sequences=lambda name: [],
        dispatch_key=lambda key: False,
    )
    chat._input = SimpleNamespace(
        text="", cursor=0, set_text=lambda *a, **k: None
    )
    chat._render = lambda: None
    chat._try_input_action = lambda key: False
    return chat


class TestCtrlCExit(unittest.TestCase):
    def test_double_ctrl_c_exits(self) -> None:
        chat = make_chat()
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertTrue(chat._running)
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertFalse(chat._running)

    def test_single_ctrl_c_shows_exit_hint(self) -> None:
        chat = make_chat()
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertTrue(chat._running)
        self.assertTrue(
            any("to exit" in m for m in chat._toast_mgr.messages)
        )

    def test_slow_double_ctrl_c_does_not_exit(self) -> None:
        chat = make_chat()
        asyncio.run(chat._handle_key("ctrl+c"))
        # Simulate the first press having happened beyond the window
        chat._last_ctrl_c_time -= chat._double_ctrl_c_threshold + 0.1
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertTrue(chat._running)

    def test_ctrl_l_opens_actual_session_picker_action(self) -> None:
        chat = make_chat()
        opened = []

        async def show_picker(theme):
            opened.append(theme)
            return False

        chat._theme_signal = SimpleNamespace(get=lambda: "theme")
        chat._show_session_picker = show_picker

        asyncio.run(chat._handle_key("ctrl+l"))

        self.assertEqual(opened, ["theme"])

    def test_ctrl_c_while_streaming_cancels_turn(self) -> None:
        chat = make_chat(streaming=True)
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertTrue(chat._running)
        self.assertFalse(chat._is_streaming.get())
        self.assertTrue(chat._streaming_cancelled)
        self.assertTrue(
            any("to exit" in m for m in chat._toast_mgr.messages)
        )

    def test_double_ctrl_c_while_streaming_exits(self) -> None:
        chat = make_chat(streaming=True)
        asyncio.run(chat._handle_key("ctrl+c"))
        chat._is_streaming.set(True)  # pretend another turn started
        asyncio.run(chat._handle_key("ctrl+c"))
        self.assertFalse(chat._running)
        self.assertFalse(chat._is_streaming.get())


if __name__ == "__main__":
    unittest.main()
