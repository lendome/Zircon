"""
Declarative binding definitions with descriptions and defaults.

Every binding has a name, a default key sequence (comma-separated alternatives),
and a human-readable description. Descriptions power the help dialog and
which-key discovery panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


BASE_MODE = "base"
MODAL_MODE = "modal"
AUTOCOMPLETE_MODE = "autocomplete"


@dataclass(frozen=True)
class Binding:
    """A single keybinding definition."""

    default: str
    description: str = ""
    slash_name: str | None = None
    slash_aliases: list[str] = field(default_factory=list)


Definitions: dict[str, Binding] = {
    # ── App ──────────────────────────────────────────────────────────
    "leader": Binding("<leader>", "Leader key for keybind combinations"),
    "app_exit": Binding("ctrl+c,ctrl+d,<leader>q", "Exit the application"),
    "app_help": Binding("<leader>?", "Show help"),
    "app_which_key": Binding("<leader>/", "Show all keybindings"),

    # ── Sessions ─────────────────────────────────────────────────────
    "session_list": Binding("<leader>l", "List all sessions"),
    "session_new": Binding("<leader>n", "Create a new session"),
    "session_share": Binding("<leader>s", "Share session"),
    "session_rename": Binding("<leader>r", "Rename session"),
    "session_fork": Binding("<leader>f", "Fork session"),
    "session_compact": Binding("<leader>c", "Compact session context"),
    "session_undo": Binding("ctrl+z", "Undo last action"),
    "session_redo": Binding("ctrl+y,ctrl+shift+z", "Redo last action"),
    "session_sidebar_toggle": Binding("<leader>b", "Toggle sidebar"),
    "session_toggle_thinking": Binding("<leader>t", "Toggle thinking display"),
    "session_page_up": Binding("pageup", "Scroll up one page"),
    "session_page_down": Binding("pagedown", "Scroll down one page"),
    "session_half_page_up": Binding("ctrl+u", "Scroll up half page"),
    "session_half_page_down": Binding("ctrl+d", "Scroll down half page"),

    # ── Models ───────────────────────────────────────────────────────
    "model_list": Binding("<leader>m", "List available models"),
    "model_select": Binding("enter", "Select model"),

    # ── Agent ────────────────────────────────────────────────────────
    "agent_cycle": Binding("tab", "Next agent"),
    "agent_cycle_reverse": Binding("shift+tab", "Previous agent"),

    # ── Input editing (Emacs-style) ──────────────────────────────────
    "input_move_left": Binding("left,ctrl+b", "Move cursor left"),
    "input_move_right": Binding("right,ctrl+f", "Move cursor right"),
    "input_line_home": Binding("home,ctrl+a", "Move to start of line"),
    "input_line_end": Binding("end,ctrl+e", "Move to end of line"),
    "input_word_forward": Binding("alt+f,alt+right,ctrl+right", "Move word forward"),
    "input_word_backward": Binding("alt+b,alt+left,ctrl+left", "Move word backward"),
    "input_delete_char_forward": Binding("delete", "Delete character forward"),
    "input_delete_char_backward": Binding("backspace,ctrl+h", "Delete character backward"),
    "input_delete_word_forward": Binding("alt+d,alt+delete,ctrl+delete", "Delete word forward"),
    "input_delete_word_backward": Binding("ctrl+w,ctrl+backspace,alt+backspace", "Delete word backward"),
    "input_delete_to_line_end": Binding("ctrl+k", "Delete to end of line"),
    "input_delete_to_line_start": Binding("ctrl+u", "Delete to start of line"),
    "input_delete_line": Binding("ctrl+shift+d", "Delete entire line"),
    "input_undo": Binding("ctrl+z", "Undo input"),
    "input_redo": Binding("ctrl+y,ctrl+shift+z", "Redo input"),
    "input_select_all": Binding("ctrl+a,ctrl+shift+a", "Select all input"),
    "input_select_left": Binding("shift+left", "Extend selection left"),
    "input_select_right": Binding("shift+right", "Extend selection right"),
    "input_select_word_forward": Binding("ctrl+shift+right,alt+shift+right", "Select word forward"),
    "input_select_word_backward": Binding("ctrl+shift+left,alt+shift+left", "Select word backward"),
    "input_select_home": Binding("shift+home", "Select to start of line"),
    "input_select_end": Binding("shift+end", "Select to end of line"),
    "input_newline": Binding("shift+return,ctrl+return,alt+return,ctrl+j", "Insert newline"),
    "input_submit": Binding("return", "Submit input"),

    # ── Slash commands ──────────────────────────────────────────────
    "slash_help": Binding("/help", "Show help", slash_name="help"),
    "slash_approve": Binding("/approve", "Approve a pending plan", slash_name="approve"),
    "slash_task": Binding("/task", "Run a full agent task", slash_name="task"),
    "slash_reset": Binding("/reset", "Clear context", slash_name="reset"),
    "slash_status": Binding("/status", "Show workspace state", slash_name="status"),
    "slash_tier": Binding("/tier", "Display tier", slash_name="tier"),
    "slash_fast": Binding("/fast", "Toggle fast mode", slash_name="fast"),
    "slash_models": Binding("/models", "Choose models by role", slash_name="models"),
    "slash_sessions": Binding("/sessions", "Resume a saved session", slash_name="sessions", slash_aliases=["continue"]),
    "slash_resume": Binding("/resume", "Resume the most recent session", slash_name="resume"),
    "slash_compact": Binding("/compact", "Compact conversation history", slash_name="compact", slash_aliases=["summarize"]),
    "slash_plugins": Binding("/plugins", "Show configured plugins", slash_name="plugins"),
    "slash_exit": Binding("/exit", "Quit", slash_name="exit"),

    # ── Navigation ───────────────────────────────────────────────────
    "scroll_up": Binding("up,k", "Scroll up"),
    "scroll_down": Binding("down,j", "Scroll down"),
    "scroll_top": Binding("g,g", "Scroll to top"),
    "scroll_bottom": Binding("G", "Scroll to bottom"),

    # ── Mouse ────────────────────────────────────────────────────────
    "mouse_click_select": Binding("mouse_left", "Click to select"),
    "mouse_copy_selection": Binding("mouse_right", "Copy selection"),
}
