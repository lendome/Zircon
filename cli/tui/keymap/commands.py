"""
Command system — bindings map to commands, commands have handlers.

Binding names and command names are separate concepts. A binding
(`session_list`) maps to a command (`session.list`). This indirection
lets you remap keys without touching command logic.

Commands can also have a `slash_name` that makes them available as
/slash commands in the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Command:
    """A command that can be dispatched by a keybinding."""

    name: str
    description: str = ""
    handler: Callable[..., Any] | None = None
    slash_name: str | None = None
    slash_aliases: list[str] = field(default_factory=list)


class CommandMap:
    """Maps binding names to commands and provides dispatch."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._binding_to_command: dict[str, str] = {}

    def register(self, binding_name: str, command: Command) -> None:
        """Register a command for a binding name."""
        self._commands[command.name] = command
        self._binding_to_command[binding_name] = command.name

    def register_many(self, mapping: dict[str, str]) -> None:
        """Register multiple binding -> command name mappings.

        Args:
            mapping: dict of binding_name -> command_name
        """
        for binding_name, command_name in mapping.items():
            cmd = self._commands.get(command_name)
            if cmd is None:
                cmd = Command(name=command_name)
                self._commands[command_name] = cmd
            self._binding_to_command[binding_name] = command_name

    def get(self, binding_name: str) -> Command | None:
        command_name = self._binding_to_command.get(binding_name)
        if command_name is None:
            return None
        return self._commands.get(command_name)

    def get_by_name(self, command_name: str) -> Command | None:
        return self._commands.get(command_name)

    def get_slash_commands(self) -> list[Command]:
        """Return all commands that have a slash_name."""
        return [c for c in self._commands.values() if c.slash_name]

    def dispatch(self, binding_name: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch the command for a binding name."""
        command = self.get(binding_name)
        if command is None:
            return None
        return dispatch_command(command, *args, **kwargs)

    def dispatch_by_name(self, command_name: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch a command by its name."""
        command = self.get_by_name(command_name)
        if command is None:
            return None
        return dispatch_command(command, *args, **kwargs)

    @property
    def all_commands(self) -> dict[str, Command]:
        return dict(self._commands)


def dispatch_command(command: Command, *args: Any, **kwargs: Any) -> Any:
    """Execute a command's handler if it has one."""
    if command.handler is not None:
        return command.handler(*args, **kwargs)
    return None


def build_default_command_map() -> CommandMap:
    """Build the default command map from Definitions + slash names."""
    from .definitions import Definitions

    cm = CommandMap()
    for binding_name, defn in Definitions.items():
        command_name = binding_name.replace("_", ".")
        cmd = Command(
            name=command_name,
            description=defn.description,
            slash_name=defn.slash_name,
            slash_aliases=defn.slash_aliases,
        )
        cm.register(binding_name, cmd)
    return cm
