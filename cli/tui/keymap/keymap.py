"""
Keymap — the central keybinding manager with mode stack, leader key,
binding gather, and conditional bindings.

The keymap:
  1. Holds resolved keybindings (name -> list of key sequences)
  2. Maintains a mode stack for context-sensitive bindings
  3. Supports a leader key with configurable timeout
  4. Dispatches key events to the matching command
  5. Provides binding gathering by context groups
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..reactive.signal import Signal, signal, computed
from .definitions import Definitions, BASE_MODE, Binding
from .aliases import normalize_key_sequence
from .parser import parse_keybinds
from .commands import CommandMap, build_default_command_map


@dataclass
class ModeBinding:
    """A binding registered in a specific mode."""

    binding_name: str
    key_sequences: list[str]
    mode: str
    enabled: Callable[[], bool] | None = None
    group: str = ""


class ModeStack:
    """LIFO stack of modes for context-sensitive keybindings."""

    def __init__(self) -> None:
        self._stack: list[str] = [BASE_MODE]

    @property
    def current(self) -> str:
        return self._stack[-1]

    @property
    def base(self) -> str:
        return self._stack[0]

    def push(self, mode: str) -> Callable[[], None]:
        """Push a mode. Returns a pop function."""
        self._stack.append(mode)
        def _pop() -> None:
            if mode in self._stack:
                self._stack.remove(mode)
        return _pop

    def pop(self) -> str | None:
        if len(self._stack) > 1:
            return self._stack.pop()
        return None

    def reset(self) -> None:
        self._stack = [BASE_MODE]


class Keymap:
    """
    Central keybinding manager.

    Usage:
        keymap = Keymap()
        keymap.configure_overrides(user_keybinds)
        keymap.on_key("ctrl+c", lambda: app.exit())

        # Mode-scoped bindings
        use_bindings(keymap, mode=BASE_MODE, bindings=["session_list", "session_new"])

        # Leader key with timeout
        register_timed_leader(keymap, trigger="<leader>", timeout_ms=2000)
    """

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = overrides or {}
        self._resolved = parse_keybinds(self._overrides)
        self._mode_stack = ModeStack()
        self._command_map = build_default_command_map()
        self._mode_bindings: dict[str, list[ModeBinding]] = {}
        self._global_bindings: list[ModeBinding] = []
        self._leader_active = signal(False)
        self._leader_timeout_ms: int = 2000
        self._leader_trigger: str = ""
        self._leader_time: float = 0.0
        self._key_handlers: list[Callable[[str], bool]] = []
        self._expanders: list[Callable[[str], list[str]]] = []

    @property
    def mode_stack(self) -> ModeStack:
        return self._mode_stack

    @property
    def leader_active(self) -> Signal[bool]:
        return self._leader_active

    @property
    def command_map(self) -> CommandMap:
        return self._command_map

    def configure_overrides(self, overrides: dict[str, str]) -> None:
        """Apply user keybind overrides (with validation)."""
        self._overrides = overrides
        self._resolved = parse_keybinds(overrides)

    def get_key_sequences(self, binding_name: str) -> list[str]:
        """Get the key sequences for a binding name."""
        return self._resolved.get(binding_name, [])

    def get_description(self, binding_name: str) -> str:
        """Get the description for a binding name."""
        defn = Definitions.get(binding_name)
        return defn.description if defn else ""

    def register_binding(
        self,
        binding_name: str,
        mode: str = BASE_MODE,
        enabled: Callable[[], bool] | None = None,
        group: str = "",
    ) -> None:
        """Register a binding in a specific mode."""
        key_seqs = self._resolved.get(binding_name, [])
        mb = ModeBinding(
            binding_name=binding_name,
            key_sequences=key_seqs,
            mode=mode,
            enabled=enabled,
            group=group,
        )
        if mode == BASE_MODE and group == "":
            self._global_bindings.append(mb)
        else:
            self._mode_bindings.setdefault(mode, []).append(mb)

    def gather(self, group: str, binding_names: list[str]) -> dict[str, list[str]]:
        """Gather bindings by group name. Returns name -> key sequences."""
        result: dict[str, list[str]] = {}
        for name in binding_names:
            result[name] = self._resolved.get(name, [])
        return result

    def register_handler(self, handler: Callable[[str], bool]) -> None:
        """Register a raw key handler. Return True to consume."""
        self._key_handlers.append(handler)

    def append_binding_expander(self, expander: Callable[[str], list[str]]) -> None:
        """Register a custom binding string expander."""
        self._expanders.append(expander)

    def dispatch_key(self, key: str) -> bool:
        """Dispatch a key event. Returns True if consumed."""
        # Expand aliases
        normalized = normalize_key_sequence(key)

        # Run expanders
        for expander in self._expanders:
            expanded = expander(normalized)
            if expanded:
                for exp_key in expanded:
                    if self._try_dispatch(exp_key):
                        return True
                return False

        return self._try_dispatch(normalized)

    def _try_dispatch(self, key: str) -> bool:
        """Try to dispatch a key in the current mode."""
        # Leader key handling
        if self._leader_active.get() and self._leader_trigger and key != self._leader_trigger:
            # Look for a two-key binding: <leader> + key
            combined = f"{self._leader_trigger}+{key}"
            for mb in self._get_active_bindings():
                if combined in mb.key_sequences and self._is_enabled(mb):
                    self._leader_active.set(False)
                    self._command_map.dispatch(mb.binding_name)
                    return True
            # Timeout or no match — cancel leader
            if time.monotonic() - self._leader_time > self._leader_timeout_ms / 1000:
                self._leader_active.set(False)
            return False

        # Check if this is the leader trigger (only if trigger is configured)
        if self._leader_trigger and key == self._leader_trigger and not self._leader_active.get():
            self._leader_active.set(True)
            self._leader_time = time.monotonic()
            return True

        # Raw handlers
        for handler in self._key_handlers:
            if handler(key):
                return True

        # Mode-scoped bindings
        for mb in self._get_active_bindings():
            if key in mb.key_sequences and self._is_enabled(mb):
                self._command_map.dispatch(mb.binding_name)
                return True

        return False

    def _get_active_bindings(self) -> list[ModeBinding]:
        """Get all bindings active in the current mode."""
        result = list(self._global_bindings)
        for mode in reversed(self._mode_stack._stack):
            result.extend(self._mode_bindings.get(mode, []))
        return result

    @staticmethod
    def _is_enabled(mb: ModeBinding) -> bool:
        if mb.enabled is None:
            return True
        return mb.enabled()

    def get_command_slashes(self) -> list[dict[str, Any]]:
        """Get slash commands derived from the keymap."""
        commands = self._command_map.get_slash_commands()
        result: list[dict[str, Any]] = []
        for cmd in commands:
            if cmd.slash_name:
                entry: dict[str, Any] = {
                    "display": f"/{cmd.slash_name}",
                    "on_select": lambda c=cmd: self._command_map.dispatch_by_name(c.name),
                }
                if cmd.slash_aliases:
                    entry["aliases"] = [f"/{a}" for a in cmd.slash_aliases]
                result.append(entry)
        return result


def use_bindings(
    keymap: Keymap,
    bindings: list[str],
    mode: str = BASE_MODE,
    enabled: Callable[[], bool] | None = None,
    group: str = "",
) -> None:
    """Register a set of bindings in a mode."""
    for name in bindings:
        keymap.register_binding(name, mode=mode, enabled=enabled, group=group)


def register_timed_leader(
    keymap: Keymap,
    trigger: str = "<leader>",
    name: str = "leader",
    timeout_ms: int = 2000,
) -> None:
    """Register a leader key with timeout.

    After pressing the leader, the user has `timeout_ms` to press the
    next key. The pending state is reactive (keymap.leader_active).
    """
    keymap._leader_trigger = trigger
    keymap._leader_timeout_ms = timeout_ms
