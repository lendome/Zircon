"""
Tier handler — switch or display the execution tier at runtime.

    zircon tier                 # show the daemon's current tier
    zircon tier fast            # switch to the low/fast tier
    zircon tier balanced        # switch to the balanced tier
    zircon tier quality         # switch to the quality tier

Requires a running daemon (start one with `zircon service start`).
"""

from __future__ import annotations

import sys

from ._shared import resolve_workspace
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    from ...daemon.transport import resolve_tier_name

    workspace = resolve_workspace(args, ctx)
    daemon = DaemonService(workspace)
    name = args.positional[0] if args.positional else ""

    if name:
        resolved = resolve_tier_name(name)
        if resolved is None:
            print(
                f"Unknown tier: {name!r}. Choices: fast, balanced, quality",
                file=sys.stderr,
            )
            return 1

    if not daemon.is_running():
        print(f"\033[91m[stopped]\033[0m Daemon not running")
        print(f"  Start one with: zircon service start")
        if name:
            print(
                f"  (tier is set at startup via --low / --quality flags instead)"
            )
        return 1

    info = daemon.info()
    assert info is not None

    transport = await daemon.transport()
    if transport is None:
        print(f"\033[91mCould not connect to daemon\033[0m", file=sys.stderr)
        return 1

    try:
        if not name:
            status = await transport.get_status()
            current = status.get("tier", "?")
            print(f"\033[92m[running]\033[0m Daemon at {info.address} (pid {info.pid})")
            print(f"  Tier:     {current}")
            print(f"  Choices:  fast, balanced, quality")
            return 0

        result = await transport.set_tier(name)
        if not result.get("ok"):
            print(
                f"\033[91mFailed to switch tier: {result.get('error', 'unknown error')}\033[0m",
                file=sys.stderr,
            )
            return 1
        new_tier = result.get("tier", "?")
        print(f"\033[92mTier switched to: {new_tier}\033[0m")
        return 0
    finally:
        await transport.close()
