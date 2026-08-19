"""
Default handler — launches the TUI.

This is the thin CLI's primary job: ensure a daemon is running (or run
in-process), get a transport, and hand off to run_tui(transport). All UI
logic lives in the tui/ package.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ._shared import resolve_workspace, resolve_tier, create_agent
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    tier = resolve_tier(args)
    swarm = bool(args.get("swarm"))
    plan_mode = bool(args.get("plan-mode"))
    verbose = bool(args.get("verbose"))
    fast_mode = bool(args.get("fast"))

    daemon = DaemonService(workspace)

    if daemon.is_running():
        transport = await daemon.transport()
        if transport is not None:
            if fast_mode:
                await transport.set_fast_mode(True)
            return await _launch_tui_with_transport(transport, workspace)

    agent = create_agent(
        repo_path=workspace,
        tier=tier,
        swarm_mode=swarm,
        plan_mode=plan_mode,
        verbose=verbose,
        fast_mode=fast_mode,
    )

    # Arm the destructive-command approval gate for in-process CLI use. The
    # coordinator is shared with the TUI so the user is prompted in-terminal.
    # (When a daemon is already running, that daemon armed its own gate.)
    from ...approval import ApprovalCoordinator

    coordinator = ApprovalCoordinator()
    agent.approval_coordinator = coordinator
    agent.approval_gate.set_handler(coordinator.request)
    agent.approval_gate.enable()

    from ...daemon.transport import LocalTransport
    transport = LocalTransport(agent)
    return await _launch_tui_with_transport(transport, workspace)


async def _launch_tui_with_transport(transport: object, workspace: str) -> int:
    from ...tui import run_tui

    try:
        await run_tui(transport, workspace)
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if hasattr(transport, "close"):
            await transport.close()  # type: ignore[attr-defined]
