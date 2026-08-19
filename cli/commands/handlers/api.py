"""
API handler — headless JSON-RPC mode on stdin/stdout.

Reads newline-delimited JSON requests from stdin, writes responses to stdout.
Useful for editor integrations and scripting.
"""

from __future__ import annotations

import asyncio
import json
import sys

from ._shared import resolve_workspace, resolve_tier, create_agent
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.transport import LocalTransport


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    tier = resolve_tier(args)
    swarm = bool(args.get("swarm"))
    plan_mode = bool(args.get("plan-mode"))

    agent = create_agent(repo_path=workspace, tier=tier, swarm_mode=swarm, plan_mode=plan_mode)
    transport = LocalTransport(agent)

    loop = asyncio.get_event_loop()

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _respond({"id": "", "type": "error", "data": {"error": "invalid JSON"}})
            continue
        await _handle(transport, req)

    return 0


async def _handle(transport: LocalTransport, req: dict) -> None:
    msg_id = req.get("id", "")
    method = req.get("method", "")
    params = req.get("params", {})

    try:
        if method == "chat_stream":
            async for chunk in transport.chat_stream(params.get("message", "")):
                _respond({"id": msg_id, "type": "data", "data": chunk})
            _respond({"id": msg_id, "type": "done"})
        elif method == "solve_stream":
            async for event in transport.solve_stream(params.get("task", "")):
                _respond({"id": msg_id, "type": "data", "data": event})
            _respond({"id": msg_id, "type": "done"})
        elif method == "submit_feedback":
            result = await transport.submit_feedback(params.get("feedback", ""))
            _respond({"id": msg_id, "type": "done", "data": result})
        elif method == "get_status":
            result = await transport.get_status()
            _respond({"id": msg_id, "type": "done", "data": result})
        elif method == "reset_context":
            result = await transport.reset_context()
            _respond({"id": msg_id, "type": "done", "data": result})
        else:
            _respond({"id": msg_id, "type": "error",
                      "data": {"error": f"unknown method: {method}"}})
    except Exception as exc:
        _respond({"id": msg_id, "type": "error", "data": {"error": str(exc)}})


def _respond(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()
