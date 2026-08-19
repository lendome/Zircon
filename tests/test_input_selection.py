from __future__ import annotations

from types import SimpleNamespace

from zirconAgent.cli.tui.components.chat import ChatComponent
from zirconAgent.cli.tui.keymap.input_bindings import InputBindings
from zirconAgent.cli.tui.keymap.definitions import Definitions


def test_backspace_deletes_selected_text():
    editor = InputBindings()
    editor.set_text("hello world")
    editor.set_selection(6, 11)

    editor.delete_char_backward()

    assert editor.text == "hello "
    assert editor.cursor == 6
    assert not editor.has_selection


def test_forward_delete_deletes_selected_text():
    editor = InputBindings()
    editor.set_text("hello world")
    editor.set_selection(0, 5)

    editor.delete_char_forward()

    assert editor.text == " world"
    assert editor.cursor == 0
    assert not editor.has_selection


def test_active_chat_backspace_deletes_selection():
    chat = ChatComponent.__new__(ChatComponent)
    chat._input = InputBindings()
    chat._input.set_text("select this text")
    chat._input.set_selection(7, 11)
    chat._pastes = {}
    chat._autocomplete = SimpleNamespace(hide=lambda: None)
    bindings = {
        name: [key.strip() for key in definition.default.split(",")]
        for name, definition in Definitions.items()
    }
    chat._keymap = SimpleNamespace(get_key_sequences=lambda name: bindings.get(name, []))

    assert chat._try_input_action("backspace")
    assert chat._input.text == "select  text"
    assert not chat._input.has_selection


def test_mouse_drag_selects_wrapped_prompt_text():
    chat = ChatComponent.__new__(ChatComponent)
    chat._input = InputBindings()
    chat._input.set_text("abcdefghij")
    chat._input.set_cursor(10)
    chat._prompt_origin_row = 5
    chat._prompt_render_width = 8
    chat._mouse_selection_anchor = None
    chat._is_streaming = SimpleNamespace(get=lambda: False)
    chat._autocomplete = SimpleNamespace(hide=lambda: None)
    chat._palette = SimpleNamespace(is_visible=False)
    chat._model_picker = None
    chat._session_picker = None
    chat._reasoning_picker = None
    chat._checkpoint_picker = None
    chat._render = lambda: None

    # Prefix consumes columns 1-2. Text wraps after six visible characters.
    chat._handle_mouse("mouse:down:0:4:5")
    chat._handle_mouse("mouse:drag:0:4:6")
    chat._handle_mouse("mouse:up:0:4:6")

    assert chat._input.get_selection() == "bcdefgh"


def test_mouse_hit_testing_uses_unicode_cell_width():
    chat = ChatComponent.__new__(ChatComponent)
    chat._input = InputBindings()
    chat._input.set_text("a界b")
    chat._input.set_cursor(3)
    chat._prompt_origin_row = 2
    chat._prompt_render_width = 20

    # Prefix is two cells: a starts at col 3, the wide char at col 4, b at 6.
    assert chat._prompt_offset_at(6, 2) == 2
