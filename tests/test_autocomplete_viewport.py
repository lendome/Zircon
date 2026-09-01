"""Tests for the autocomplete dropdown's palette-style viewport scrolling."""

from cli.tui.autocomplete.autocomplete import Autocomplete, AutocompleteOption
from cli.tui.autocomplete.triggers import TriggerType


def _make(n: int) -> Autocomplete:
    ac = Autocomplete()
    ac._state.visible = True
    ac._state.trigger = TriggerType.SLASH
    ac._state.options = [
        AutocompleteOption(display=f"/cmd{i:02d}", value=f"/cmd{i:02d}", category="command")
        for i in range(n)
    ]
    return ac


class TestViewport:
    def test_short_list_fully_visible(self):
        ac = _make(3)
        assert ac.visible_window == (0, 3)
        assert ac.height == 3

    def test_long_list_clipped_to_window(self):
        ac = _make(20)
        assert ac.visible_window == (0, 8)
        assert ac.height == 8

    def test_selection_scrolls_window_down(self):
        ac = _make(20)
        for _ in range(8):
            ac.move_down()
        # selected == 8 -> last visible slot of the first window
        assert ac.state.selected == 8
        assert ac.visible_window == (1, 9)
        ac.move_down()
        assert ac.state.selected == 9
        assert ac.visible_window == (2, 10)

    def test_selection_scrolls_window_back_up(self):
        ac = _make(20)
        ac._state.selected = 10
        ac._state.visible_start = 3
        ac.move_up()
        assert ac.state.selected == 9
        # still inside [3, 11) — window start unchanged (palette semantics)
        assert ac.visible_window == (3, 11)

    def test_last_item_window_pinned_to_end(self):
        ac = _make(20)
        ac._state.selected = 19
        ac._ensure_selection_visible()
        assert ac.visible_window == (12, 20)

    def test_page_down_jumps_by_window(self):
        ac = _make(20)
        ac.page_down()
        assert ac.state.selected == 8
        # selection crossed the window edge -> start slides to 1 (palette semantics)
        assert ac.visible_window == (1, 9)
        ac.page_down()
        assert ac.state.selected == 16
        assert ac.visible_window == (9, 17)

    def test_page_up_clamps_at_zero(self):
        ac = _make(20)
        ac._state.selected = 5
        ac.page_up()
        assert ac.state.selected == 0
        assert ac.visible_window == (0, 8)

    def test_page_down_clamps_at_end(self):
        ac = _make(9)
        ac.page_down()
        assert ac.state.selected == 8
        # window pinned to end, selection visible
        start, end = ac.visible_window
        assert start <= 8 < end

    def test_refilter_resets_window(self):
        ac = _make(20)
        ac._state.selected = 15
        ac._state.visible_start = 8
        ac._filter_slash("")  # refilter resets selection + window
        assert ac.state.selected == 0
        assert ac.state.visible_start == 0

    def test_hide_resets_window(self):
        ac = _make(20)
        ac._state.selected = 15
        ac._state.visible_start = 8
        ac.hide()
        assert ac.state.visible_start == 0
        assert ac.state.selected == 0

    def test_wrap_no_crash_empty_options(self):
        ac = Autocomplete()
        ac.move_up()
        ac.move_down()
        ac.page_up()
        ac.page_down()
        assert ac.visible_window == (0, 0)
