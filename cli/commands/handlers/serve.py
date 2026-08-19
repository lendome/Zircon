"""
Serve handler — start the daemon server in the foreground.

The server runs in the current process. It writes a lock file so the
CLI's service commands can discover and manage it.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from ._shared import resolve_workspace, resolve_tier, create_agent
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.server import DaemonServer
from ...daemon.service import DaemonService, DaemonInfo


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    tier = resolve_tier(args)
    swarm = bool(args.get("swarm"))
    plan_mode = bool(args.get("plan-mode"))
    verbose = bool(args.get("verbose"))

    port = int(args.get("port", 0) or 0)
    host = str(args.get("host", "127.0.0.1"))

    agent = create_agent(
        repo_path=workspace,
        tier=tier,
        swarm_mode=swarm,
        plan_mode=plan_mode,
        verbose=verbose,
    )

    server = DaemonServer(agent, host=host, port=port)
    await server.start()

    # Arm the approval gate: the daemon pushes mid-stream approval requests to
    # the connected TUI and awaits the side-channel response. This only fires
    # for destructive git/db commands, and only because the CLI started this
    # daemon.
    agent.approval_gate.set_handler(server.request_approval)
    agent.approval_gate.enable()

    daemon = DaemonService(workspace)
    daemon.write_lock(DaemonInfo(pid=os.getpid(), port=server.port, host=host))

    print(f"Zircon daemon listening on {server.address}", flush=True)
    print(f"Workspace: {workspace}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.clear_lock()
        await server.stop()

    return 0
