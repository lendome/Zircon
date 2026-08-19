"""
Command palette & slash commands.

Unifies all actions under a single command palette (Ctrl+P) with fuzzy
search, categories, suggested commands, and shortcut display. Slash
commands are auto-derived from commands that have a slash_name.
"""

from __future__ import annotations

from .fuzzy import fuzzy_score, fuzzy_rank
from .registry import CommandRegistry, Command, CommandEntry
from .palette import CommandPalette

__all__ = [
    "fuzzy_score",
    "fuzzy_rank",
    "CommandRegistry",
    "Command",
    "CommandEntry",
    "CommandPalette",
]
