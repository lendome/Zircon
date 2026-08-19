"""
Keybinding system — declarative bindings, mode stack, leader key, which-key,
and full text-editing bindings.

Bindings are separated from commands: a binding (`session_list`) maps to a
command (`session.list`). Keys can be remapped without touching logic.
"""

from __future__ import annotations

from .definitions import Binding, Definitions, BASE_MODE
from .aliases import KeyAliases, expand_key_aliases
from .parser import parse_keybinds, parse_binding_string
from .commands import CommandMap, Command, dispatch_command
from .keymap import Keymap, ModeStack, use_bindings, register_timed_leader
from .which_key import WhichKeyPanel
from .input_bindings import InputBindings, get_input_binding_definitions

__all__ = [
    "Binding",
    "Definitions",
    "BASE_MODE",
    "KeyAliases",
    "expand_key_aliases",
    "parse_keybinds",
    "parse_binding_string",
    "CommandMap",
    "Command",
    "dispatch_command",
    "Keymap",
    "ModeStack",
    "use_bindings",
    "register_timed_leader",
    "WhichKeyPanel",
    "InputBindings",
    "get_input_binding_definitions",
]
