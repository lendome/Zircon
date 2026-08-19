"""Tests for clearing the transient prompt UI before output is committed."""

from __future__ import annotations

import asyncio
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
import sys
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.cli.tui.components.chat import ChatComponent, TextBuffer
from zirconAgent.cli.tui.prompt.footer import PromptFooter
from zirconAgent.cli.tui.session.lifecycle import SessionInfo
from zirconAgent.cli.tui.theming.themes import get_theme


class TestChatRendering(unittest.TestCase):
    def test_clear_render_removes_previous_prompt_region(self) -> None:
        chat = ChatComponent.__new__(ChatComponent)
        chat._render_lines = 3
        output = io.StringIO()

        with patch("zirconAgent.cli.tui.components.chat.sys.stdout", output):
            chat._clear_render()

        self.assertEqual(output.getvalue(), "\033[3A\033[J")
        self.assertEqual(chat._render_lines, 0)

    def test_completed_chat_resets_progress_footer_to_ready(self) -> None:
        class Signal:
            def __init__(self, value=None):
                self._value = value

            def set(self, value):
                self._value = value

            def get(self):
                if self._value is not None:
                    return self._value

                class Color:
                    def to_rich(self) -> str:
                        return "green"

                class Theme:
                    success = Color()
                    info = Color()
                    text_muted = Color()

                return Theme()

        class Transport:
            async def chat_stream(self, _message: str):
                yield {"status": "completed", "done": True}

        chat = ChatComponent.__new__(ChatComponent)
        chat._theme_signal = Signal()
        chat._transport = Transport()
        chat._is_streaming = Signal(False)
        chat._streaming_cancelled = False
        chat._footer = PromptFooter()
        chat._footer.update(is_active=True, status_message="Contacting LLM...")
        chat.console = Console(file=io.StringIO())
        chat._data = type("Data", (), {"refresh": lambda self, transport: asyncio.sleep(0)})()
        chat._refresh_workspace_state = lambda: asyncio.sleep(0)
        chat._update_footer = lambda: chat._footer.update(
            is_active=False,
            status_message="Ready",
            show_interrupt=False,
        )

        asyncio.run(chat._stream_chat("hello", TextBuffer()))

        self.assertFalse(chat._footer.data.is_active)
        self.assertEqual(chat._footer.data.status_message, "Ready")
        self.assertFalse(chat._footer.data.show_interrupt)

    def test_footer_tracks_active_session_identity(self) -> None:
        chat = ChatComponent.__new__(ChatComponent)
        chat._theme_signal = type("Signal", (), {"get": lambda self: get_theme("tokyo-night")})()
        chat._footer = PromptFooter()
        chat._active_session = SessionInfo(id="abc123def456", title="Restore old sessions")
        chat._data = type("Data", (), {
            "status": {
                "model": "test-model",
                "provider": "local",
                "session_cost_usd": 0.0,
                "context_used_tokens": 8_000,
                "context_max_tokens": 32_000,
            }
        })()
        chat._transport = type("Transport", (), {"info": object()})()

        chat._update_footer()

        self.assertEqual(chat._footer.data.session_title, "Restore old sessions")
        self.assertEqual(chat._footer.data.session_id, "abc123def456")
        self.assertEqual(chat._footer.data.context_used_tokens, 8_000)
        self.assertEqual(chat._footer.data.context_max_tokens, 32_000)

        output = io.StringIO()
        console = Console(file=output, width=140, force_terminal=False)
        console.print(chat._footer.render())
        self.assertIn("ctx 8,000/32,000 (25%)", output.getvalue())

    def test_resume_replaces_workspace_before_replaying_transcript(self) -> None:
        events = []
        session = SessionInfo(id="old-session", title="Older work", status="completed")

        class Lifecycle:
            resumed_messages = [{"role": "user", "content": "old message"}]

            async def resume(self, session_id):
                events.append(("resume", session_id))
                return session

        class Registry:
            def get(self, name):
                if name == "session_lifecycle":
                    return Lifecycle()
                return type("Project", (), {"workspace": "workspace"})()

        chat = ChatComponent.__new__(ChatComponent)
        chat.registry = Registry()
        chat._data = type("Data", (), {"refresh": lambda self, transport: asyncio.sleep(0)})()
        chat._transport = object()
        chat._toast_mgr = type("Toast", (), {
            "success": lambda self, message: events.append(("success", message)),
            "warning": lambda self, message: events.append(("warning", message)),
            "error": lambda self, message: events.append(("error", message)),
        })()
        chat._clear_render = lambda: events.append("clear")
        chat.console = type("Console", (), {"clear": lambda self: events.append("screen-clear")})()
        chat._print_session_header = lambda active, count: events.append(("header", active.id, count))
        chat._render_prior_messages = lambda messages, render_prompt=True: events.append(
            ("messages", messages, render_prompt)
        )
        chat._render = lambda: events.append("render")
        chat._update_footer = lambda: events.append("footer")
        chat._render_lines = 4

        result = asyncio.run(chat._resume_and_render("old-session"))

        self.assertTrue(result)
        self.assertEqual(chat._active_session, session)
        self.assertEqual(chat._restored_message_count, 1)
        self.assertLess(events.index("screen-clear"), events.index(("messages", Lifecycle.resumed_messages, False)))
        self.assertEqual(events[-1][0], "success")
        self.assertIn("render", events)


if __name__ == "__main__":
    unittest.main()
