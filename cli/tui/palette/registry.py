"""
Command registry — central store for all commands from any component.

Commands are plain data objects with:
  - name: unique dotted identifier (session.list, model.cycle_recent)
  - title: human-readable label
  - category: grouping in the palette (Session, Agent, System, etc.)
  - slash_name: if set, creates a /slash command
  - slash_aliases: alternative slash names
  - suggested: boolean or function; surfaced first in the palette
  - hidden: registered but not shown in palette (internal)
  - enabled: function for dynamic enable/disable
  - desc: description for help/which-key
  - run: the action to execute
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Command:
    """A single command registered in the palette."""

    name: str
    title: str = ""
    category: str = "General"
    slash_name: str | None = None
    slash_aliases: list[str] = field(default_factory=list)
    suggested: bool | Callable[[], bool] = False
    hidden: bool = False
    enabled: Callable[[], bool] | None = None
    desc: str = ""
    run: Callable[..., Any] | None = None
    key_binding: str = ""

    def is_suggested(self) -> bool:
        if callable(self.suggested):
            return self.suggested()
        return bool(self.suggested)

    def is_enabled(self) -> bool:
        if self.enabled is None:
            return True
        return self.enabled()


@dataclass
class CommandEntry:
    """A command entry in the palette with its binding info."""

    command: Command
    binding: str = ""

    @property
    def footer(self) -> str:
        """Formatted key binding for display."""
        return self.binding or self.command.key_binding or ""


class CommandRegistry:
    """
    Central command registry. Components register commands through this.

    Commands are namespaced so the palette can query by namespace
    (e.g., "palette" for visible commands, internal for hidden ones).
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._namespaces: dict[str, set[str]] = {}

    def register(self, command: Command, namespace: str = "palette") -> None:
        """Register a command in a namespace."""
        self._commands[command.name] = command
        self._namespaces.setdefault(namespace, set()).add(command.name)

    def register_many(self, commands: list[Command], namespace: str = "palette") -> None:
        """Register multiple commands."""
        for cmd in commands:
            self.register(cmd, namespace)

    def unregister(self, name: str) -> None:
        """Remove a command by name."""
        self._commands.pop(name, None)
        for names in self._namespaces.values():
            names.discard(name)

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def get_entries(
        self,
        namespace: str = "palette",
        include_hidden: bool = False,
    ) -> list[CommandEntry]:
        """Get all command entries in a namespace."""
        names = self._namespaces.get(namespace, set())
        entries: list[CommandEntry] = []
        for name in names:
            cmd = self._commands.get(name)
            if cmd is None:
                continue
            if cmd.hidden and not include_hidden:
                continue
            entries.append(CommandEntry(command=cmd, binding=cmd.key_binding))
        return entries

    def get_slash_commands(self) -> list[Command]:
        """Get all commands that have a slash_name."""
        return [c for c in self._commands.values() if c.slash_name and not c.hidden]

    def dispatch(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch a command by name."""
        cmd = self.get(name)
        if cmd is None:
            return None
        if not cmd.is_enabled():
            return None
        if cmd.run is not None:
            return cmd.run(*args, **kwargs)
        return None

    def get_visible_commands(self) -> list[Command]:
        """Get all visible (non-hidden, enabled) commands."""
        return [
            c for c in self._commands.values()
            if not c.hidden and c.is_enabled()
        ]
