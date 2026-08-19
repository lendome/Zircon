"""
service restart — restart the background daemon.
"""

from __future__ import annotations

import sys

from .._shared import resolve_workspace
from ....runtime import ParsedArgs, RuntimeContext
from ....daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    port = int(args.get("port", 0) or 0)

    daemon = DaemonService(workspace)
    try:
        info = daemon.restart(port=port)
        print(f"Daemon restarted at {info.address} (pid {info.pid})")
        return 0
    except RuntimeError as exc:
        print(f"Failed to restart daemon: {exc}", file=sys.stderr)
        return 1
