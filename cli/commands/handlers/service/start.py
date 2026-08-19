"""
service start — start the background daemon.
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
    if daemon.is_running():
        info = daemon.info()
        assert info is not None
        print(f"Daemon already running at {info.address} (pid {info.pid})")
        return 0

    try:
        info = daemon.start(port=port)
        print(f"Daemon started at {info.address} (pid {info.pid})")
        return 0
    except RuntimeError as exc:
        print(f"Failed to start daemon: {exc}", file=sys.stderr)
        return 1
