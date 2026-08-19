"""
Autocomplete — the main autocomplete manager.

Handles trigger detection, fuzzy filtering, frecency boosting,
mode stack integration, directory expansion, and positioning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..palette.fuzzy import fuzzy_score
from ..reactive.signal import Signal, signal
from .triggers import detect_trigger, extract_line_range, TriggerType, LineRange
from .file_search import AsyncFileSearch, FileSearchResult


@dataclass
class AutocompleteOption:
    display: str = ""
    value: str = ""
    category: str = ""
    is_directory: bool = False
    on_select: Callable[[], None] | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutocompleteState:
    visible: bool = False
    trigger: TriggerType = TriggerType.NONE
    index: int = 0
    selected: int = 0
    options: list[AutocompleteOption] = field(default_factory=list)
    input_mode: str = "keyboard"
    anchor_x: int = 0
    anchor_y: int = 0
    anchor_width: int = 0


class Autocomplete:
    """
    Main autocomplete manager.

    - detect(): check for @ or / trigger at cursor
    - filter(): fuzzy-filter options by query
    - select(): execute the selected option
    - expand_directory(): Tab to drill into a directory
    - show()/hide(): control visibility

    When visible, pushes "autocomplete" mode on the keymap stack.
    """

    def __init__(
        self,
        file_search: AsyncFileSearch | None = None,
        frecency: Any = None,
        slash_commands: list[dict] | None = None,
    ) -> None:
        self._file_search = file_search or AsyncFileSearch(frecency=frecency)
        self._frecency = frecency
        self._slash_commands = slash_commands or []
        self._state = AutocompleteState()
        self._state_signal: Signal[bool] = signal(False)
        self._on_select: Callable[[AutocompleteOption], None] | None = None

    @property
    def is_visible(self) -> bool:
        return self._state.visible

    @property
    def state(self) -> AutocompleteState:
        return self._state

    @property
    def visible_signal(self) -> Signal[bool]:
        return self._state_signal

    def set_select_handler(self, handler: Callable[[AutocompleteOption], None]) -> None:
        self._on_select = handler

    def set_slash_commands(self, commands: list[dict]) -> None:
        self._slash_commands = commands

    def detect(self, text: str, cursor: int) -> bool:
        """Check for trigger and open autocomplete if needed. Returns True if visible."""
        if self._state.visible:
            # Close conditions
            if cursor <= self._state.index:
                self.hide()
                return False
            between = text[self._state.index + 1:cursor]
            if " " in between:
                self.hide()
                return False
            if self._state.trigger == TriggerType.SLASH:
                # Close if command is followed by arg
                import re
                if re.match(r"^\S+\s+\S+\s*$", text[:cursor]):
                    self.hide()
                    return False
            return True

        trigger, idx = detect_trigger(text, cursor)
        if trigger == TriggerType.NONE:
            return False

        self._state.trigger = trigger
        self._state.index = idx
        self._state.selected = 0
        self._state.visible = True
        self._state_signal.set(True)
        return True

    def hide(self) -> None:
        self._state.visible = False
        self._state.options = []
        self._state.selected = 0
        self._state.trigger = TriggerType.NONE
        self._state_signal.set(False)

    def move_up(self) -> None:
        if self._state.options:
            self._state.selected = max(0, self._state.selected - 1)
            self._state.input_mode = "keyboard"

    def move_down(self) -> None:
        if self._state.options:
            self._state.selected = min(len(self._state.options) - 1, self._state.selected + 1)
            self._state.input_mode = "keyboard"

    def select(self) -> bool:
        """Execute the selected option."""
        if not self._state.visible or not self._state.options:
            return False
        if self._state.selected >= len(self._state.options):
            return False
        opt = self._state.options[self._state.selected]
        # Always call our select handler first (for text replacement),
        # then the option's own on_select if present (for command dispatch)
        if self._on_select is not None:
            self._on_select(opt)
        elif opt.on_select is not None:
            opt.on_select()
        self.hide()
        return True

    def expand_directory(self) -> bool:
        """Tab expansion — if selected item is a directory, expand into it."""
        if not self._state.visible or not self._state.options:
            return False
        opt = self._state.options[self._state.selected]
        if not opt.is_directory:
            return False
        # The caller handles the actual text manipulation
        return True

    async def filter(self, query: str, directory: str = ".") -> None:
        """Filter options based on the current trigger and query."""
        if not self._state.visible:
            return

        # Strip the trigger character from the query
        search = query[self._state.index + 1:] if self._state.index < len(query) else ""

        if self._state.trigger == TriggerType.FILE:
            await self._filter_files(search, directory)
        elif self._state.trigger == TriggerType.SLASH:
            self._filter_slash(search)

    async def _filter_files(self, query: str, directory: str) -> None:
        """Filter file options with async search + frecency boosting."""
        base_query, line_range = extract_line_range(query)

        results = await self._file_search.search(base_query, directory)

        options: list[AutocompleteOption] = []
        for r in results:
            display = r.display
            if line_range.has_range:
                display = f"{display}{line_range.label}"
            options.append(AutocompleteOption(
                display=display,
                value=r.path,
                category="file",
                is_directory=r.is_directory,
                data={"path": r.path, "line_range": line_range},
            ))

        self._state.options = options
        self._state.selected = 0
        self._state.input_mode = "keyboard"

    def _filter_slash(self, query: str) -> None:
        """Filter slash command options with fuzzy matching."""
        q = query.lower()
        scored: list[tuple[AutocompleteOption, float]] = []
        for cmd in self._slash_commands:
            display = cmd.get("display", "")
            desc = cmd.get("description", "")
            score = fuzzy_score(q, display)
            if score > 0 or not q:
                scored.append((AutocompleteOption(
                    display=display,
                    value=display,
                    category="command",
                    on_select=cmd.get("on_select"),
                    data={"description": desc},
                ), score if q else 1.0))
        scored.sort(key=lambda x: x[1], reverse=True)
        self._state.options = [o for o, _ in scored[:20]]
        self._state.selected = 0
        self._state.input_mode = "keyboard"

    @property
    def selected_option(self) -> AutocompleteOption | None:
        if 0 <= self._state.selected < len(self._state.options):
            return self._state.options[self._state.selected]
        return None

    @property
    def height(self) -> int:
        count = len(self._state.options)
        return min(10, max(1, count))
