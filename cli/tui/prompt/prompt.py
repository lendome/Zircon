"""
Prompt — the main input component with structured inline content.

The prompt is a full-featured editor. Text, file mentions, pastes, and
attachments are tracked as typed parts with extmark positions. When
the user edits text, extmark positions shift and parts are reconciled.

Modes:
  - normal: standard prompt input
  - shell:  shell command mode (! prefix)
  - autocomplete: autocomplete dropdown is visible
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..keymap.input_bindings import InputBindings
from .extmarks import ExtmarkManager, Extmark


class PromptMode(str, Enum):
    NORMAL = "normal"
    SHELL = "shell"
    AUTOCOMPLETE = "autocomplete"


@dataclass
class PromptPart:
    """A structured part of the prompt (text, file, attachment, paste)."""

    type: str  # "text", "file", "agent", "paste", "attachment", "image"
    text: str = ""
    filename: str = ""
    url: str = ""
    mime: str = ""
    source_start: int = 0
    source_end: int = 0
    virtual_text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Prompt:
    """
    The main prompt input with structured inline content tracking.

    Combines:
      - InputBindings for Emacs-style text editing
      - ExtmarkManager for virtual text regions
      - PromptParts for structured content (files, pastes, attachments)
      - Mode switching (normal, shell, autocomplete)
      - Submit guard against double-submission
    """

    def __init__(self) -> None:
        self.input = InputBindings()
        self.extmarks = ExtmarkManager()
        self.parts: list[PromptPart] = []
        self._extmark_to_part: dict[int, int] = {}
        self.mode: PromptMode = PromptMode.NORMAL
        self._submitting: bool = False
        self._on_submit: Callable[[str, list[PromptPart]], Any] | None = None
        self._autocomplete_index: int = -1
        self._autocomplete_visible: bool = False
        self._autocomplete_trigger: str = ""
        self._editor_context: str | None = None

    @property
    def text(self) -> str:
        return self.input.text

    @property
    def cursor_offset(self) -> int:
        return self.input.cursor

    @property
    def is_empty(self) -> bool:
        return len(self.input.text.strip()) == 0

    @property
    def is_submitting(self) -> bool:
        return self._submitting

    def set_submit_handler(self, handler: Callable[[str, list[PromptPart]], Any]) -> None:
        self._on_submit = handler

    def clear(self) -> None:
        """Clear the prompt input and all parts/extmarks."""
        self.input.set_text("")
        self.extmarks.clear()
        self.parts.clear()
        self._extmark_to_part.clear()
        self.mode = PromptMode.NORMAL
        self._autocomplete_visible = False

    def insert_file_mention(self, filename: str, url: str = "") -> None:
        """Insert a @mention for a file, creating an extmark and part."""
        cursor = self.cursor_offset
        virtual_text = f"@{filename}"
        self.input.insert(virtual_text + " ")

        em = self.extmarks.create(
            start=cursor,
            end=cursor + len(virtual_text),
            virtual=True,
            style="file_mention",
            type="prompt_part",
            data={"virtual_text": virtual_text},
        )

        part = PromptPart(
            type="file",
            filename=filename,
            url=url,
            source_start=cursor,
            source_end=cursor + len(virtual_text),
            virtual_text=virtual_text,
        )
        self.parts.append(part)
        self._extmark_to_part[em.id] = len(self.parts) - 1

    def insert_paste(self, text: str, virtual_text: str = "") -> None:
        """Insert pasted content as an extmark-tracked part."""
        cursor = self.cursor_offset
        if virtual_text:
            display = virtual_text
        else:
            line_count = text.count("\n") + 1
            if line_count > 1:
                display = f"[Pasted {line_count} lines]"
            elif len(text) > 150:
                display = f"[Pasted {len(text)} chars]"
            else:
                display = text

        self.input.insert(display + " ")

        em = self.extmarks.create(
            start=cursor,
            end=cursor + len(display),
            virtual=True,
            style="paste",
            type="prompt_part",
            data={"virtual_text": display, "full_text": text},
        )

        part = PromptPart(
            type="paste",
            text=text,
            source_start=cursor,
            source_end=cursor + len(display),
            virtual_text=display,
        )
        self.parts.append(part)
        self._extmark_to_part[em.id] = len(self.parts) - 1

    def insert_attachment(self, filename: str, mime: str, content: bytes | str = "") -> None:
        """Insert an attachment (image, PDF, binary) as an extmark."""
        cursor = self.cursor_offset
        ext = filename.rsplit(".", 1)[-1].upper() if "." in filename else "FILE"
        display = f"[{ext}: {filename}]"

        self.input.insert(display + " ")

        em = self.extmarks.create(
            start=cursor,
            end=cursor + len(display),
            virtual=True,
            style="attachment",
            type="prompt_part",
            data={"virtual_text": display},
        )

        part = PromptPart(
            type="attachment",
            filename=filename,
            mime=mime,
            source_start=cursor,
            source_end=cursor + len(display),
            virtual_text=display,
            data={"content": content},
        )
        self.parts.append(part)
        self._extmark_to_part[em.id] = len(self.parts) - 1

    def sync_extmarks(self) -> None:
        """Reconcile extmark positions with parts after text edits."""
        orphaned = self.extmarks.reconcile(self.input.text)
        for em_id in orphaned:
            part_idx = self._extmark_to_part.pop(em_id, None)
            if part_idx is not None and part_idx < len(self.parts):
                self.parts.pop(part_idx)
                # Re-index remaining mappings
                for k in list(self._extmark_to_part.keys()):
                    if self._extmark_to_part[k] > part_idx:
                        self._extmark_to_part[k] -= 1

        # Update part positions from extmarks
        for em in self.extmarks.get_all(type="prompt_part"):
            part_idx = self._extmark_to_part.get(em.id)
            if part_idx is not None and part_idx < len(self.parts):
                self.parts[part_idx].source_start = em.start
                self.parts[part_idx].source_end = em.end

    def set_editor_context(self, context: str | None) -> None:
        """Set or clear the editor context (auto-attached selection)."""
        self._editor_context = context

    @property
    def editor_context(self) -> str | None:
        return self._editor_context

    def detect_autocomplete_trigger(self) -> str | None:
        """Detect if the cursor is after a @ or / trigger."""
        text = self.input.text
        cursor = self.cursor_offset

        if cursor == 0:
            return None

        # Scan backward from cursor for trigger character
        for i in range(cursor - 1, -1, -1):
            ch = text[i]
            if ch in ("@", "/"):
                # Check no whitespace between trigger and cursor
                between = text[i + 1:cursor]
                if " " not in between and "\n" not in between:
                    return ch
                return None
            if ch in (" ", "\n", "\t"):
                return None
        return None

    async def submit(self) -> bool:
        """Submit the prompt. Guards against double-submission."""
        if self._submitting:
            return False
        if self.is_empty:
            return False

        self._submitting = True
        try:
            self.sync_extmarks()
            text = self.input.text
            parts = list(self.parts)
            if self._on_submit is not None:
                result = self._on_submit(text, parts)
                if hasattr(result, "__await__"):
                    await result
            self.clear()
            return True
        finally:
            self._submitting = False

    def enter_shell_mode(self) -> None:
        """Switch to shell mode (! prefix)."""
        self.mode = PromptMode.SHELL
        if not self.input.text.startswith("!"):
            self.input.set_text("!" + self.input.text, cursor=1)

    def exit_shell_mode(self) -> None:
        """Exit shell mode back to normal."""
        self.mode = PromptMode.NORMAL
        if self.input.text.startswith("!"):
            self.input.set_text(self.input.text[1:], cursor=max(0, self.cursor_offset - 1))

    def is_shell_mode(self) -> bool:
        return self.mode == PromptMode.SHELL
