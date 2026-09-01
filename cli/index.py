"""
Thin CLI entry point — the entire binary.

Does three things:
  1. Parse arguments via the declarative spec tree
  2. Manage daemon lifecycle (ensure running, get transport)
  3. Lazy-load and dispatch to the matching handler

All UI logic lives in the tui/ package. All business logic lives in the
daemon/server. This file is intentionally thin.

Equivalent of OpenCode's cli/src/index.ts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .runtime import Runtime, RuntimeContext
from .spec import build_root_spec


def _enable_windows_ansi() -> None:
    """Enable VT processing on stdout before anything renders.

    Classic conhost (Windows PowerShell 5.1, plain cmd) ships with
    ENABLE_VIRTUAL_TERMINAL_PROCESSING off, so every ANSI escape would
    print literally. Windows Terminal/VS Code already have it on and this
    is a no-op there. Mirrors enable_win32_vt_output() in
    cli/tui/platform/platform.py — inlined here to avoid pulling the whole
    TUI package into the thin entry point before onboarding renders.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            desired = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if desired != mode.value:
                kernel32.SetConsoleMode(handle, ctypes.c_ulong(desired))
    except Exception:
        pass


def _ensure_imports() -> None:
    """Make zirconAgent importable as a package from handler modules."""
    here = Path(__file__).resolve().parent  # .../zirconAgent/cli
    parent = str(here.parent)                # .../zirconAgent
    grandparent = str(here.parent.parent)    # .../slopsite
    # Add grandparent so `import zirconAgent` works; add parent as fallback
    # for bare `from core.X import Y` style (legacy). The grandparent must
    # come first so `zirconAgent.core` resolves correctly.
    for p in [grandparent, parent]:
        if p not in sys.path:
            sys.path.insert(0, p)


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point. Returns an exit code.

    Walks the command spec tree, lazy-loads the matching handler, and
    dispatches. Handlers are async — we run them on a single event loop.
    """
    _ensure_imports()
    _enable_windows_ansi()

    raw_args = list(argv if argv is not None else sys.argv[1:])

    # Do not construct an Agent or connect to a daemon until a provider and
    # role models have been configured on first use.
    if raw_args not in ([], ["--help"], ["-h"], ["help"]) and raw_args[:1] != ["status"]:
        from .onboarding import ensure_configured

        if not ensure_configured():
            return 1

    if not raw_args or raw_args == ["--help"] or raw_args == ["-h"]:
        return asyncio.run(_dispatch(["help"]))

    return asyncio.run(_dispatch(raw_args))


async def _dispatch(argv: list[str]) -> int:
    runtime = Runtime(build_root_spec(), handler_base="cli")

    workspace = "."
    parsed = runtime.resolve(argv)
    if parsed.positional and not parsed.spec.name == "task":
        candidate = Path(parsed.positional[0])
        if candidate.is_dir():
            workspace = str(candidate.resolve())

    ctx = RuntimeContext(workspace=workspace)

    try:
        return await runtime.dispatch(argv, ctx)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
