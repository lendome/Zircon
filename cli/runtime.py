"""
Runtime — walks the command spec tree, matches user input, lazy-loads handlers.

This is the Python equivalent of OpenCode's Runtime.handlers() system.
Handlers are loaded via importlib on first use, keeping startup fast —
only the code for the invoked subcommand is loaded.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .spec import Spec, Param, build_root_spec


@dataclass
class ParsedArgs:
    """Result of parsing argv against a spec node."""

    spec: Spec
    flags: dict[str, bool] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    positional: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)

    def get(self, name: str, default: Any = None) -> Any:
        if name in self.flags and self.flags[name]:
            return True
        if name in self.options:
            return self.options[name]
        return default

    @property
    def path(self) -> str:
        parts: list[str] = []
        node = self.spec
        while node:
            parts.append(node.name)
            node = None
        return " ".join(reversed(parts))


class SpecMatcher:
    """Walks the spec tree to find the matching command node."""

    def match(self, root: Spec, argv: list[str]) -> tuple[Spec, list[str]]:
        """Walk the tree consuming subcommand names. Returns (node, remaining)."""
        node = root
        rest = list(argv)

        while rest:
            child = node.find(rest[0])
            if child is None:
                break
            node = child
            rest = rest[1:]

        return node, rest


class ArgParser:
    """Parses remaining args against a spec node's params."""

    def parse(self, spec: Spec, args: list[str]) -> ParsedArgs:
        result = ParsedArgs(spec=spec)
        positional_params = [p for p in spec.params if p.positional]
        pos_idx = 0

        i = 0
        while i < len(args):
            arg = args[i]

            if arg.startswith("--"):
                name = arg[2:]
                if "=" in name:
                    key, val = name.split("=", 1)
                    result.options[key] = self._coerce(spec, key, val)
                else:
                    param = self._find_param(spec, name)
                    if param and param.is_flag:
                        result.flags[name] = True
                    elif param and i + 1 < len(args):
                        result.options[name] = self._coerce(spec, name, args[i + 1])
                        i += 1
                    else:
                        result.options[name] = True
            elif arg.startswith("-") and len(arg) == 2:
                name = arg[1:]
                param = self._find_param(spec, name)
                if param and param.is_flag:
                    result.flags[name] = True
                else:
                    result.positional.append(arg)
            else:
                if pos_idx < len(positional_params):
                    param = positional_params[pos_idx]
                    if param.variadic:
                        result.positional.append(arg)
                    else:
                        result.positional.append(arg)
                        pos_idx += 1
                else:
                    result.positional.append(arg)
            i += 1

        return result

    def _find_param(self, spec: Spec, name: str) -> Param | None:
        for p in spec.params:
            if p.name == name:
                return p
        return None

    def _coerce(self, spec: Spec, name: str, value: str) -> Any:
        param = self._find_param(spec, name)
        if param and param.choices and value not in param.choices:
            raise ValueError(f"Invalid value for --{name}: {value}. Choices: {param.choices}")
        if param and isinstance(param.default, int):
            try:
                return int(value)
            except ValueError:
                return value
        return value


class Runtime:
    """
    The command runtime. Holds the spec tree and dispatches to handlers.

    Handlers are lazy-loaded: each handler is a module with an async
    `run(args: ParsedArgs, ctx: RuntimeContext) -> int` function.
    The module is imported on first dispatch via importlib.
    """

    def __init__(self, root_spec: Spec | None = None, handler_base: str = "") -> None:
        self.root = root_spec or build_root_spec()
        self._handler_base = handler_base
        self._matcher = SpecMatcher()
        self._parser = ArgParser()
        self._handler_cache: dict[str, Callable[..., Any]] = {}

    def resolve(self, argv: list[str]) -> ParsedArgs:
        """Resolve argv to a spec node + parsed args."""
        node, remaining = self._matcher.match(self.root, argv)
        parsed = self._parser.parse(node, remaining)
        return parsed

    def load_handler(self, handler_path: str) -> Callable[..., Any]:
        """Lazy-load a handler module by dotted path. Cached after first load."""
        if handler_path in self._handler_cache:
            return self._handler_cache[handler_path]

        full_path = f"{self._handler_base}.{handler_path}" if self._handler_base else handler_path
        module = importlib.import_module(full_path)
        run_fn = getattr(module, "run", None)
        if run_fn is None:
            raise AttributeError(f"Handler {full_path} has no `run` function")
        self._handler_cache[handler_path] = run_fn
        return run_fn

    async def dispatch(self, argv: list[str], ctx: RuntimeContext) -> int:
        """Match argv to a handler and invoke it. Returns exit code."""
        parsed = self.resolve(argv)

        if parsed.spec.handler is None:
            if parsed.spec.has_subcommands():
                self._print_subcommand_help(parsed.spec)
                return 1
            print(f"No handler for: {parsed.spec.name}", file=sys.stderr)
            return 1

        try:
            handler = self.load_handler(parsed.spec.handler)
        except (ImportError, AttributeError) as exc:
            print(f"Failed to load handler {parsed.spec.handler}: {exc}", file=sys.stderr)
            return 1

        ctx.parsed = parsed
        result = handler(parsed, ctx)
        if hasattr(result, "__await__"):
            return await result  # type: ignore[no-any-return]
        return result  # type: ignore[no-any-return]

    def _print_subcommand_help(self, spec: Spec) -> None:
        print(f"{spec.name}: {spec.description}\n")
        print("Subcommands:")
        for path, child in spec.flatten(spec.name):
            if child is spec:
                continue
            print(f"  {path:<30} {child.description}")
        print()


@dataclass
class RuntimeContext:
    """
    Shared context passed to every handler.

    Holds runtime-wide state: the workspace path, global flags, the daemon
    service handle, and the resolved parsed args. Handlers read from this
    rather than reaching into global state.
    """

    workspace: str = "."
    global_flags: dict[str, bool] = field(default_factory=dict)
    daemon_service: Any = None
    parsed: ParsedArgs | None = None
    extra: dict[str, Any] = field(default_factory=dict)
