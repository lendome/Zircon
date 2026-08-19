"""
Declarative command specification tree.

Each node has a name, optional params (flags/arguments), optional subcommands,
and a handler path (dotted module path lazy-loaded by the runtime).

    Spec.make("zircon", {
        "description": "Zircon CLI",
        "handler": "commands.handlers.default",
        "commands": [
            Spec.make("service", {
                "description": "Manage the background daemon",
                "commands": [
                    Spec.make("start", {"description": "Start the daemon",
                                         "handler": "commands.handlers.service.start"}),
                    Spec.make("stop",  {"description": "Stop the daemon",
                                         "handler": "commands.handlers.service.stop"}),
                ],
            }),
        ],
    })

The runtime walks this tree, matches user input, and lazy-loads the
matching handler module via importlib — keeping startup fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Param:
    """A single CLI parameter (flag or positional argument)."""

    name: str
    description: str = ""
    flag: bool = False
    default: Any = None
    choices: list[str] | None = None
    positional: bool = False
    variadic: bool = False

    @property
    def is_flag(self) -> bool:
        return self.flag


@dataclass
class Spec:
    """A command spec node in the declarative command tree."""

    name: str
    description: str = ""
    handler: str | None = None
    params: list[Param] = field(default_factory=list)
    commands: list[Spec] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    hidden: bool = False

    @staticmethod
    def make(name: str, **kwargs: Any) -> Spec:
        """Factory mirroring OpenCode's Spec.make()."""
        commands = kwargs.pop("commands", None) or []
        params = kwargs.pop("params", None) or []
        aliases = kwargs.pop("aliases", None) or []
        return Spec(
            name=name,
            description=kwargs.pop("description", ""),
            handler=kwargs.pop("handler", None),
            params=list(params),
            commands=list(commands),
            aliases=list(aliases),
            hidden=kwargs.pop("hidden", False),
        )

    def find(self, name: str) -> Spec | None:
        """Find a direct child by name or alias."""
        for child in self.commands:
            if child.name == name or name in child.aliases:
                return child
        return None

    def has_subcommands(self) -> bool:
        return bool(self.commands)

    def is_leaf(self) -> bool:
        return not self.commands

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def flatten(self, prefix: str = "") -> list[tuple[str, Spec]]:
        """Flatten the tree into (path, spec) pairs for help/completion."""
        path = f"{prefix} {self.name}".strip()
        result: list[tuple[str, Spec]] = []
        if not self.hidden:
            result.append((path, self))
        for child in self.commands:
            result.extend(child.flatten(path))
        return result


def build_root_spec() -> Spec:
    """Build the root command spec tree for the Zircon CLI."""
    return Spec.make(
        "zircon",
        description="Zircon — Autonomous Coding Agent",
        handler="commands.handlers.default",
        params=[
            Param(name="path", positional=True, description="Workspace directory"),
            Param(name="low", flag=True, description="Low tier"),
            Param(name="quality", flag=True, description="Quality tier"),
            Param(name="plan-mode", flag=True, description="Enable planning"),
            Param(name="swarm", flag=True, description="Swarm mode"),
            Param(name="fast", flag=True, description="Fast mode (highest-throughput provider routing)"),
            Param(name="verbose", flag=True, description="Verbose logging"),
            Param(name="warnings", flag=True, description="Show internal warnings"),
        ],
        commands=[
            Spec.make(
                "serve",
                description="Start the daemon server (foreground)",
                handler="commands.handlers.serve",
                params=[
                    Param(name="port", default=0, description="Port (0=auto)"),
                    Param(name="host", default="127.0.0.1", description="Bind host"),
                ],
            ),
            Spec.make(
                "task",
                description="Run a task headless (no TUI)",
                handler="commands.handlers.task",
                params=[
                    Param(name="description", positional=True, variadic=True,
                          description="Task description"),
                    Param(name="low", flag=True, description="Low tier"),
                    Param(name="quality", flag=True, description="Quality tier"),
                    Param(name="plan-mode", flag=True, description="Enable planning"),
                    Param(name="swarm", flag=True, description="Swarm mode"),
                ],
            ),
            Spec.make(
                "api",
                description="Run in headless API mode (JSON on stdin/stdout)",
                handler="commands.handlers.api",
                params=[
                    Param(name="port", default=0, description="Port (0=auto)"),
                ],
            ),
            Spec.make(
                "status",
                description="Show daemon and session status",
                handler="commands.handlers.status",
            ),
            Spec.make(
                "models",
                description="List models configured in models.yaml",
                handler="commands.handlers.models",
            ),
            Spec.make(
                "tier",
                description="Switch or show the execution tier (fast|balanced|quality)",
                handler="commands.handlers.tier",
                params=[
                    Param(
                        name="name",
                        positional=True,
                        description="Tier to switch to: fast, balanced, quality (omits = show current)",
                    ),
                ],
            ),
            Spec.make(
                "fast",
                description="Toggle fast mode (highest-throughput provider routing)",
                handler="commands.handlers.fast",
                params=[
                    Param(
                        name="state",
                        positional=True,
                        description="on | off | toggle (omit = show current)",
                    ),
                ],
            ),
            Spec.make(
                "service",
                description="Manage the background daemon",
                commands=[
                    Spec.make(
                        "start",
                        description="Start the background daemon",
                        handler="commands.handlers.service.start",
                        params=[
                            Param(name="port", default=0, description="Port (0=auto)"),
                        ],
                    ),
                    Spec.make(
                        "stop",
                        description="Stop the background daemon",
                        handler="commands.handlers.service.stop",
                    ),
                    Spec.make(
                        "restart",
                        description="Restart the background daemon",
                        handler="commands.handlers.service.restart",
                        params=[
                            Param(name="port", default=0, description="Port (0=auto)"),
                        ],
                    ),
                ],
            ),
            Spec.make(
                "tui",
                description="Launch the TUI explicitly (connects to daemon)",
                handler="commands.handlers.default",
                aliases=["chat"],
            ),
            Spec.make(
                "help",
                description="Show help",
                handler="commands.handlers.help",
                aliases=["--help", "-h"],
            ),
        ],
    )
