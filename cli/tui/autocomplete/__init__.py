"""
Autocomplete & frecency system.

Multiple trigger types: @ for files/agents/resources, / for slash commands.
Fuzzy matching with frecency boosting, line range parsing, directory
expansion, async file search, mode-aware keybinding priority.
"""

from __future__ import annotations

from .autocomplete import Autocomplete, AutocompleteState, AutocompleteOption
from .triggers import detect_trigger, TriggerType, extract_line_range
from .file_search import AsyncFileSearch

__all__ = [
    "Autocomplete",
    "AutocompleteState",
    "AutocompleteOption",
    "detect_trigger",
    "TriggerType",
    "extract_line_range",
    "AsyncFileSearch",
]
