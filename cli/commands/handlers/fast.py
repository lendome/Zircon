"""
Fast-mode handler — toggle highest-throughput provider routing at runtime.

    zircon fast                 # show current fast-mode state
    zircon fast on              # enable fast mode
    zircon fast off             # disable fast mode
    zircon fast toggle          # flip it

Fast mode adds OpenRouter's throughput sorting (equivalent to the ":nitro"
model suffix), routing each request to the fastest available provider.
Requires a running daemon (start one with `zircon service start`).
"""

from __future__ import annotations

import sys

from ._shared import resolve_workspace
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    daemon = DaemonService(workspace)
    state = (args.positional[0] if args.positional else "").lower()

    if state and state not in ("on", "off", "toggle", "true", "false"):
        print(
            f"Unknown state: {state!r}. Use: on, off, toggle",
            file=sys.stderr,
        )
        return 1

    if not daemon.is_running():
        print("\033[91m[stopped]\033[0m Daemon not running")
        print("  Start one with: zircon service start")
        print("  (or launch with the --fast flag)")
        return 1

    transport = await daemon.transport()
    if transport is None:
        print("\033[91mCould not connect to daemon\033[0m", file=sys.stderr)
        return 1

    try:
        status = await transport.get_status()
        current = bool(status.get("fast_mode", False))

        if not state:
            label = "\033[92mon\033[0m" if current else "off"
            print(f"Fast mode: {label}")
            return 0

        if state == "toggle":
            target = not current
        elif state in ("on", "true"):
            target = True
        else:
            target = False

        result = await transport.set_fast_mode(target)
        if not result.get("ok"):
            print(
                f"\033[91mFailed to set fast mode: {result.get('error', 'unknown error')}\033[0m",
                file=sys.stderr,
            )
            return 1
        label = "\033[92mon\033[0m" if target else "off"
        print(f"Fast mode: {label}")
        return 0
    finally:
        await transport.close()
