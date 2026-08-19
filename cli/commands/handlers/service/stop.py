"""
service stop — stop the background daemon.
"""

from __future__ import annotations

import sys

from .._shared import resolve_workspace
from ....runtime import ParsedArgs, RuntimeContext
from ....daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    daemon = DaemonService(workspace)

    if not daemon.is_running():
        print("Daemon is not running.")
        return 0

    info = daemon.info()
    assert info is not None
    if daemon.stop():
        print(f"Daemon stopped (was pid {info.pid} at {info.address})")
        return 0
    else:
        print("Failed to stop daemon.", file=sys.stderr)
        return 1
