"""
Daemon server — wraps an Agent instance and serves it over TCP.

Uses a simple newline-delimited JSON protocol (no external deps):
  Request:  {"id": "...", "method": "chat_stream", "params": {...}}
  Response: {"id": "...", "type": "data"|"done"|"error", "data": {...}}

Streaming methods send multiple "data" responses followed by "done".
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from .transport import LocalTransport

logger = logging.getLogger("agent.cli.daemon.server")


class DaemonServer:
    """
    Async TCP server that exposes an Agent over the wire protocol.

    The server owns the Agent instance and its event loop. The TUI connects
    via RemoteTransport and gets the same API as if it were in-process.
    """

    def __init__(
        self,
        agent: Any,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._agent = agent
        self._transport = LocalTransport(agent)
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._actual_port: int = port
        self._shutdown = asyncio.Event()
        # Connected client writers — used to push mid-stream approval
        # requests back to the TUI(s). A destructive tool call blocks inside
        # a streaming RPC; the approval request rides the same connection's
        # writer (writing is fine while the read loop is busy), and the TUI's
        # response comes back over a fresh side connection (see
        # RemoteTransport.respond_approval) to avoid a serial-dispatch deadlock.
        self._clients: set[asyncio.StreamWriter] = set()
        self._pending_approvals: dict[str, asyncio.Future] = {}

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._actual_port

    @property
    def address(self) -> str:
        return f"{self._host}:{self._actual_port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
        )
        sockets = self._server.sockets or ()
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]
        logger.info("Daemon server listening on %s", self.address)

    async def serve(self) -> None:
        """Start and serve until stopped."""
        await self.start()
        await self._shutdown.wait()

    async def stop(self) -> None:
        self._shutdown.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Daemon server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.debug("Client connected: %s", peer)
        self._clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    await self._send(writer, {"id": "", "type": "error",
                                              "data": {"error": "invalid JSON"}})
                    continue
                await self._dispatch(writer, msg)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except asyncio.CancelledError:
                pass
            logger.debug("Client disconnected: %s", peer)

    async def _dispatch(self, writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
        msg_id = msg.get("id", "")
        method = msg.get("method", "")
        params = msg.get("params", {})

        handler = getattr(self, f"_rpc_{method}", None)
        if handler is None:
            await self._send(writer, {"id": msg_id, "type": "error",
                                      "data": {"error": f"unknown method: {method}"}})
            return

        try:
            await handler(writer, msg_id, params)
        except Exception as exc:
            logger.exception("RPC error in %s", method)
            await self._send(writer, {"id": msg_id, "type": "error",
                                      "data": {"error": str(exc)}})

    async def _send(self, writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
        writer.write((json.dumps(msg) + "\n").encode())
        await writer.drain()

    # ── RPC method handlers ──────────────────────────────────────────────

    async def _rpc_chat_stream(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        message = params.get("message", "")
        async for chunk in self._transport.chat_stream(message):
            await self._send(writer, {"id": msg_id, "type": "data", "data": chunk})
        await self._send(writer, {"id": msg_id, "type": "done"})

    async def _rpc_solve_stream(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        task = params.get("task", "")
        async for event in self._transport.solve_stream(task):
            await self._send(writer, {"id": msg_id, "type": "data", "data": event})
        await self._send(writer, {"id": msg_id, "type": "done"})

    async def _rpc_submit_feedback(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.submit_feedback(params.get("feedback", ""))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_get_status(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.get_status()
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_set_tier(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        name = params.get("name", "")
        result = await self._transport.set_tier(name)
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_set_reasoning_effort(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        effort = params.get("effort", "")
        result = await self._transport.set_reasoning_effort(effort)
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_set_nitro_mode(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.set_nitro_mode(bool(params.get("enabled", False)))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_set_fast_mode(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.set_fast_mode(bool(params.get("enabled", False)))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_reset_context(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.reset_context()
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_compact_context(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.compact_context()
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_list_sessions(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.list_sessions()
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_resume_session(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.resume_session(str(params.get("session_id", "")))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_list_models(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.list_models(bool(params.get("refresh", False)))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_set_model(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.set_model(
            str(params.get("role", "default")),
            str(params.get("profile_id", "")),
            str(params.get("model_id", "")),
        )
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_create_checkpoint(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.create_checkpoint(str(params.get("label", "")))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_list_checkpoints(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.list_checkpoints(int(params.get("n", 20)))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    async def _rpc_revert_checkpoint(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        result = await self._transport.revert_checkpoint(str(params.get("sha", "")))
        await self._send(writer, {"id": msg_id, "type": "done", "data": result})

    # ── Mid-stream tool approval (destructive-command interruption) ───────
    #
    # Called by the Agent's registry gate (in the daemon process) when a tool
    # call is classified destructive. Pushes an approval_request frame to the
    # connected TUI(s) and awaits the matching response, which arrives via a
    # separate `respond_approval` RPC on a side connection.

    async def request_approval(
        self, name: str, arguments: dict[str, Any], reason: str
    ) -> bool:
        """Ask the connected TUI to approve a destructive tool call."""
        req_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_approvals[req_id] = future
        payload = {
            "id": req_id,
            "name": name,
            "arguments": arguments,
            "reason": reason,
        }
        msg = {"id": req_id, "type": "approval_request", "data": payload}
        for w in list(self._clients):
            try:
                await self._send(w, msg)
            except Exception as exc:
                logger.debug("could not push approval request to a client: %s", exc)
        try:
            return await asyncio.wait_for(future, timeout=300.0)
        except asyncio.TimeoutError:
            self._pending_approvals.pop(req_id, None)
            logger.warning("approval request %s timed out — denying", req_id)
            return False

    async def _rpc_respond_approval(
        self, writer: asyncio.StreamWriter, msg_id: str, params: dict[str, Any]
    ) -> None:
        req_id = str(params.get("id", ""))
        approved = bool(params.get("approved", False))
        future = self._pending_approvals.pop(req_id, None)
        if future is not None and not future.done():
            future.set_result(approved)
        await self._send(writer, {"id": msg_id, "type": "done", "data": {"ok": True}})
