"""_render must repaint in a single write wrapped in DEC 2026 sync markers."""

from __future__ import annotations

import sys
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.cli.tui.components.chat import ChatComponent, _SessionPicker
from zirconAgent.cli.tui.dialogs.toast import ToastManager
from zirconAgent.cli.tui.prompt.footer import PromptFooter
from zirconAgent.cli.tui.theming.themes import get_theme
from zirconAgent.cli.tui.session.lifecycle import SessionInfo


class RecordingStdout:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass


class FakeSignal:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


def make_chat() -> ChatComponent:
    chat = ChatComponent.__new__(ChatComponent)
    chat._theme_signal = FakeSignal(get_theme("tokyo-night"))
    chat._toast_mgr = ToastManager()
    chat._footer = PromptFooter()
    chat._which_key = SimpleNamespace(is_visible=False, theme=None)
    chat._autocomplete = SimpleNamespace(is_visible=False)
    chat._input = SimpleNamespace(text="hello", cursor=5)
    chat._shell = SimpleNamespace(active=False)
    chat._model_picker = None
    chat._session_picker = None
    chat._reasoning_picker = None
    chat._checkpoint_picker = None
    chat._render_lines = 0
    return chat


class TestAtomicRender(unittest.TestCase):
    def test_render_is_one_synchronized_write(self) -> None:
        chat = make_chat()
        out = RecordingStdout()

        with patch("zirconAgent.cli.tui.components.chat.sys.stdout", out):
            chat._render()

        self.assertEqual(len(out.writes), 1)
        frame = out.writes[0]
        self.assertTrue(frame.startswith("\x1b[?2026h"))
        self.assertTrue(frame.endswith("\x1b[?2026l"))

    def test_second_render_clears_previous_region_in_same_write(self) -> None:
        chat = make_chat()
        out = RecordingStdout()

        with patch("zirconAgent.cli.tui.components.chat.sys.stdout", out):
            chat._render()
            first_lines = chat._render_lines
            chat._render()

        self.assertEqual(len(out.writes), 2)
        second = out.writes[1]
        self.assertGreater(first_lines, 0)
        self.assertIn(f"\x1b[{first_lines}A", second)
        self.assertIn("\x1b[J", second)


class TestRowCounting(unittest.TestCase):
    """_count_rows must count physical rows, including wrapped lines."""

    def test_plain_lines(self) -> None:
        self.assertEqual(ChatComponent._count_rows("ab\ncd\n", 80), 2)

    def test_line_exactly_terminal_width_is_one_row(self) -> None:
        self.assertEqual(ChatComponent._count_rows("x" * 80 + "\n", 80), 1)

    def test_wrapped_line_counts_extra_rows(self) -> None:
        self.assertEqual(ChatComponent._count_rows("x" * 81 + "\n", 80), 2)
        self.assertEqual(ChatComponent._count_rows("x" * 161 + "\n", 80), 3)

    def test_ansi_codes_do_not_count_as_width(self) -> None:
        line = "\x1b[1;36m" + "x" * 80 + "\x1b[0m\n"
        self.assertEqual(ChatComponent._count_rows(line, 80), 1)


class TestPickerRegionTracking(unittest.TestCase):
    """Opening a picker via a key gesture must repaint the existing prompt
    region in place, not orphan it and paint a duplicate below."""

    def test_checkpoint_picker_empty_keeps_region(self) -> None:
        import asyncio

        chat = make_chat()
        chat._render_lines = 5  # a prompt region is on screen

        class EmptyCheckpoints:
            async def list_checkpoints(self, n):
                return []

        chat._checkpoint_mgr = EmptyCheckpoints()
        seen: list[int] = []
        chat._render = lambda: seen.append(chat._render_lines)

        asyncio.run(chat._show_checkpoint_picker())

        # _render must still see the old 5-line region so it can clear it
        self.assertEqual(seen, [5])


class TestSessionPicker(unittest.TestCase):
    def test_selected_session_stays_visible_beyond_first_page(self) -> None:
        sessions = [
            SessionInfo(
                id=f"session-{index}",
                title=f"Session {index}",
                updated_at=float(20 - index),
                status="completed",
                files_modified=index,
                is_active=index == 0,
            )
            for index in range(20)
        ]
        picker = _SessionPicker(sessions, get_theme("tokyo-night"))

        for _ in range(15):
            picker.move(1)
        output = io.StringIO()
        console = Console(file=output, width=120, force_terminal=False)
        console.print(picker.render())
        rendered = output.getvalue()

        self.assertEqual(picker.selected.id, "session-15")
        self.assertIn("Session 15", rendered)
        self.assertIn("16/20", rendered)

    def test_page_and_boundary_navigation(self) -> None:
        sessions = [
            SessionInfo(id=f"session-{index}", title=f"Session {index}", updated_at=float(30 - index))
            for index in range(30)
        ]
        picker = _SessionPicker(sessions, get_theme("tokyo-night"))

        picker.move_page(1)
        self.assertEqual(picker.index, 12)
        picker.move_end()
        self.assertEqual(picker.index, 29)
        picker.move_page(1)
        self.assertEqual(picker.index, 29)
        picker.move_home()
        self.assertEqual(picker.index, 0)

    def test_workspace_summary_exposes_active_and_recent_sessions(self) -> None:
        chat = ChatComponent.__new__(ChatComponent)
        chat._active_session = SessionInfo(
            id="active-123",
            title="Improve the TUI",
            status="running",
            files_modified=4,
            is_active=True,
        )
        chat._sessions = [
            chat._active_session,
            SessionInfo(id="recent-456", title="Older session", status="completed"),
        ]
        output = io.StringIO()
        console = Console(file=output, width=120, force_terminal=False)

        console.print(chat._workspace_summary(get_theme("tokyo-night")))
        rendered = output.getvalue()

        self.assertIn("Session Workspace", rendered)
        self.assertIn("ACTIVE", rendered)
        self.assertIn("Improve the TUI", rendered)
        self.assertIn("Older session", rendered)
        self.assertIn("Ctrl+L switch", rendered)


if __name__ == "__main__":
    unittest.main()
