"""
Transport abstraction — the wire between the TUI and the backend daemon.

Two implementations:
  - LocalTransport:  in-process direct call to an Agent instance
  - RemoteTransport: JSON-over-TCP to a running DaemonServer

Both expose the same async API so the TUI doesn't care which it's using.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

logger = logging.getLogger("agent.cli.daemon.transport")

# Canonical tier names accepted from users, including friendly aliases.
# "fast" is an alias for the "low" tier (cheap, fast, no planning).
TIER_ALIASES: dict[str, str] = {
    "fast": "low",
    "low": "low",
    "balanced": "balanced",
    "balance": "balanced",
    "quality": "quality",
    "high": "quality",
}


def resolve_tier_name(name: str) -> str | None:
    """Resolve a user-provided tier name (with aliases) to a canonical tier name.

    Returns one of "low", "balanced", "quality", or None if unknown.
    """
    return TIER_ALIASES.get(name.lower().strip())


@dataclass
class TransportInfo:
    """Metadata describing a transport connection."""

    kind: str
    address: str
    headers: dict[str, str] = None  # type: ignore[assignment]


class Transport:
    """
    Abstract transport. The TUI talks to the agent exclusively through this.

    Methods mirror the Agent's public API but return dicts / async iterators
    of dicts (JSON-serializable) so they work over the wire.
    """

    info: TransportInfo

    async def chat_stream(self, message: str) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def solve_stream(self, task: str) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def submit_feedback(self, feedback: str) -> dict[str, Any]:
        raise NotImplementedError

    async def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    async def set_tier(self, name: str) -> dict[str, Any]:
        """Switch the agent's execution tier at runtime. Returns the new tier."""
        raise NotImplementedError

    async def set_reasoning_effort(self, effort: str) -> dict[str, Any]:
        """Switch the model's reasoning effort level. Returns the new effort."""
        raise NotImplementedError

    async def set_nitro_mode(self, enabled: bool) -> dict[str, Any]:
        """Toggle OpenRouter Nitro model routing."""
        raise NotImplementedError

    async def set_fast_mode(self, enabled: bool) -> dict[str, Any]:
        """Toggle fast mode (highest-throughput provider routing)."""
        raise NotImplementedError

    async def reset_context(self) -> dict[str, Any]:
        raise NotImplementedError

    async def list_sessions(self) -> dict[str, Any]:
        raise NotImplementedError

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def compact_context(self) -> dict[str, Any]:
        raise NotImplementedError

    async def list_models(self, refresh: bool = False) -> dict[str, Any]:
        raise NotImplementedError

    async def set_model(self, role: str, profile_id: str, model_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def create_checkpoint(self, label: str = "") -> dict[str, Any]:
        """Create a git checkpoint before an agent turn."""
        raise NotImplementedError

    async def list_checkpoints(self, n: int = 20) -> dict[str, Any]:
        """List recent checkpoints (commits) for the revert picker."""
        raise NotImplementedError

    async def revert_checkpoint(self, sha: str) -> dict[str, Any]:
        """Revert the working tree to a specific checkpoint."""
        raise NotImplementedError

    async def respond_approval(self, req_id: str, approved: bool) -> dict[str, Any]:
        """Deliver the user's approve/deny decision for a pending approval."""
        raise NotImplementedError

    async def close(self) -> None:
        pass


class LocalTransport(Transport):
    """
    In-process transport — calls the Agent directly.

    Used when the TUI runs the agent in the same process (no daemon).
    Still serializes to dicts so the TUI code is identical regardless
    of transport kind.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self.info = TransportInfo(kind="local", address="in-process")

    async def chat_stream(self, message: str) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self._agent.chat_stream(message):
            yield self._chunk_to_dict(chunk)

    async def solve_stream(self, task: str) -> AsyncIterator[dict[str, Any]]:
        async for event in self._agent.solve_stream(task):
            yield self._event_to_dict(event)

    async def submit_feedback(self, feedback: str) -> dict[str, Any]:
        self._agent.submit_feedback(feedback)
        return {"ok": True}

    async def get_status(self) -> dict[str, Any]:
        ctx = self._agent.context
        model_id = ""
        provider = ""
        context_max_tokens = int(getattr(ctx, "context_window", 0) or 0)
        context_used_tokens = 0
        try:
            default_role = self._agent.router.config.default_role
            candidates = self._agent.router.select(default_role)
            if candidates:
                model_id = candidates[0].model
                url = candidates[0].base_url
                if "openrouter" in url:
                    provider = "openrouter"
                elif "anthropic" in url:
                    provider = "anthropic"
                elif "openai" in url:
                    provider = "openai"
                elif "ollama" in url or "localhost:11434" in url:
                    provider = "ollama"
                else:
                    provider = url.split("//")[-1].split("/")[0] if url else "local"
        except Exception:
            pass
        try:
            from zirconAgent.core.context import estimate_tokens

            messages = ctx.build_messages(
                self._agent._get_system_prompt(),
                self._agent.registry.tool_descriptions(),
            )
            context_used_tokens = sum(
                estimate_tokens(message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            context_used_tokens += estimate_tokens(
                json.dumps(self._agent.registry.get_schemas(), ensure_ascii=False, default=str)
            )
        except Exception:
            context_used_tokens = sum(
                len(str(message.get("content") or "")) // 4
                for message in getattr(ctx, "history", [])
                if isinstance(message, dict)
            )
        context_percent = (
            min(100.0, context_used_tokens * 100.0 / context_max_tokens)
            if context_max_tokens > 0 else 0.0
        )
        return {
            "status": self._agent.status.value,
            "working_set": len(ctx.working_set),
            "modified_files": len(ctx.modified_files),
            "session_notes": len(ctx.session_notes),
            "history": len(ctx.history),
            "context_used_tokens": context_used_tokens,
            "context_max_tokens": context_max_tokens,
            "context_percent": context_percent,
            "tier": self._agent.tier.value,
            "fast_mode": bool(getattr(getattr(self._agent, "router", None), "fast_mode", False)),
            "session_id": getattr(getattr(self._agent, "sessions", None).current, "id", ""),
            "session_cost_usd": float(getattr(getattr(self._agent, "router", None), "session_cost_usd", 0.0)),
            "model": model_id,
            "provider": provider,
            "reasoning_effort": getattr(getattr(self._agent, "tier_cfg", None), "reasoning_effort", "medium"),
            "nitro_mode": bool(getattr(getattr(self._agent, "router", None), "nitro_mode", False)),
        }

    async def set_fast_mode(self, enabled: bool) -> dict[str, Any]:
        router = getattr(self._agent, "router", None)
        if router is None or not hasattr(router, "set_fast_mode"):
            return {"ok": False, "error": "Router does not support fast mode"}
        router.set_fast_mode(enabled)
        return {"ok": True, "fast_mode": enabled}

    async def set_nitro_mode(self, enabled: bool) -> dict[str, Any]:
        router = getattr(self._agent, "router", None)
        if router is None or not hasattr(router, "set_nitro_mode"):
            return {"ok": False, "error": "Router does not support Nitro mode"}
        router.set_nitro_mode(enabled)
        return {"ok": True, "nitro_mode": enabled}

    async def reset_context(self) -> dict[str, Any]:
        self._agent.context.clear_history()
        return {"ok": True}

    async def compact_context(self) -> dict[str, Any]:
        await self._agent.context.compact_history(self._agent.router)
        return {"ok": True}

    async def list_sessions(self) -> dict[str, Any]:
        sessions = self._agent.sessions.list_sessions()
        current = self._agent.sessions.current
        return {"sessions": sessions, "active_id": current.id if current else ""}

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        session, messages = self._agent.restore_session(session_id)
        if session is None:
            return {"ok": False, "error": f"Session not found: {session_id}"}
        return {
            "ok": True,
            "session": self._session_to_dict(session),
            "history": len(messages),
            "messages": messages,
        }

    async def list_models(self, refresh: bool = False) -> dict[str, Any]:
        profiles = []
        for profile in self._agent.router.config.profiles:
            profiles.append({
                "id": profile.name,
                "model": profile.model,
                "base_url": profile.base_url,
                "roles": list(profile.roles),
                "context_window": profile.context_window,
                "max_tokens": profile.max_tokens,
                "timeout": profile.timeout,
            })
        catalog: dict[str, list[str]] = {}
        if refresh:
            for profile in self._agent.router.config.profiles:
                try:
                    catalog[profile.name] = await self._fetch_provider_models(profile)
                except Exception as exc:
                    logger.warning("Could not scan models for %s: %s", profile.name, exc)
        return {
            "profiles": profiles,
            "roles": sorted({role for profile in profiles for role in profile["roles"]} | set(self._agent.router.config.role_priority)),
            "default_role": self._agent.router.config.default_role,
            "role_priority": self._agent.router.config.role_priority,
            "catalog": catalog,
        }

    async def set_model(self, role: str, profile_id: str, model_id: str) -> dict[str, Any]:
        from zirconAgent.core.config import _DEFAULT_CONFIG_PATH, save_config
        import yaml

        profile = next((p for p in self._agent.router.config.profiles if p.name == profile_id), None)
        if profile is None:
            return {"ok": False, "error": f"Unknown model profile: {profile_id}"}
        if not model_id.strip():
            return {"ok": False, "error": "Model ID cannot be empty"}

        profile.model = model_id.strip()
        self._agent.router.set_role_profile(role, profile_id)

        try:
            raw = yaml.safe_load(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            profiles = raw.setdefault("profiles", {})
            if profile_id not in profiles:
                return {"ok": False, "error": f"Profile is missing from models.yaml: {profile_id}"}
            profiles[profile_id]["model"] = profile.model
            router = raw.setdefault("router", {})
            priority = list(router.setdefault("role_priority", {}).get(role, []))
            router["role_priority"][role] = [profile_id, *[name for name in priority if name != profile_id]]
            save_config(raw, _DEFAULT_CONFIG_PATH)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return {"ok": False, "error": f"Could not save models.yaml: {exc}"}

        return {"ok": True, "role": role, "profile_id": profile_id, "model": profile.model}

    @staticmethod
    def _session_to_dict(session: Any) -> dict[str, Any]:
        return {
            "id": session.id,
            "task": session.task,
            "status": str(session.status),
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "files_modified": list(session.files_modified),
            "tokens_used": session.tokens_used,
            "cost_usd": session.cost_usd,
        }

    @staticmethod
    async def _fetch_provider_models(profile: Any) -> list[str]:
        """Fetch an OpenAI-compatible v1/models catalog without exposing credentials."""
        import httpx

        headers = {"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {}
        async with httpx.AsyncClient(timeout=min(float(profile.timeout), 30.0)) as client:
            response = await client.get(
                f"{profile.base_url.rstrip('/')}/models",
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return sorted({str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")})

    async def set_tier(self, name: str) -> dict[str, Any]:
        import copy

        resolved = resolve_tier_name(name)
        if resolved is None:
            return {
                "ok": False,
                "error": f"Unknown tier: {name!r}. Choices: fast, balanced, quality",
            }

        from zirconAgent.core.types import Tier, TIER_PRESETS

        new_tier = Tier(resolved)
        agent = self._agent
        current = getattr(agent, "tier_cfg", None)

        # Deep-copy the preset so we never mutate the shared TIER_PRESETS instance.
        new_cfg = copy.deepcopy(TIER_PRESETS.get(new_tier, TIER_PRESETS[Tier.BALANCED]))

        # Preserve runtime overrides the user enabled via flags/env.
        if current is not None:
            if getattr(current, "swarm_mode", False):
                new_cfg.swarm_mode = True
            if not getattr(current, "plans_disabled", True):
                new_cfg.plans_disabled = False

        agent.tier = new_tier
        agent.tier_cfg = new_cfg

        context = getattr(agent, "context", None)
        if context is not None:
            context.context_window = new_cfg.context_window
            context.max_tokens = max(0, new_cfg.context_window - context.safety_margin)

        # Propagate the new TierConfig to every component that holds a
        # reference (they all store it as `.tier`). Sub-agents receive the
        # config at creation time, so they pick up the change on next use.
        for comp in (
            getattr(agent, "context", None),
            getattr(agent, "planner", None),
            getattr(agent, "executor", None),
            getattr(agent, "_gatekeeper", None),
            getattr(agent, "_advisor", None),
        ):
            if comp is not None:
                try:
                    comp.tier = new_cfg
                except AttributeError:
                    pass

        executor = getattr(agent, "executor", None)
        if executor is not None:
            from zirconAgent.core.context_window_guard import ContextWindowGuard
            from zirconAgent.core.trajectory_diet import TrajectoryPruner

            executor._ctx_guard = ContextWindowGuard(
                tier_config=new_cfg,
                context_window=new_cfg.context_window,
            )
            executor._trajectory_pruner = TrajectoryPruner(
                tier_config=new_cfg,
                context_window=new_cfg.context_window,
            )

        logger.info("Tier switched to %s", new_tier.value)
        return {
            "ok": True,
            "tier": new_tier.value,
            "context_window": new_cfg.context_window,
        }

    async def set_reasoning_effort(self, effort: str) -> dict[str, Any]:
        VALID_EFFORTS = {"max", "xhigh", "high", "medium", "low", "minimal", "none"}
        effort = effort.lower().strip()
        if effort not in VALID_EFFORTS:
            return {
                "ok": False,
                "error": f"Invalid reasoning effort: {effort!r}. Choices: {', '.join(sorted(VALID_EFFORTS))}",
            }
        agent = self._agent
        agent.tier_cfg.reasoning_effort = effort
        agent.router.reasoning_effort = effort
        logger.info("Reasoning effort switched to %s", effort)
        return {"ok": True, "effort": effort}

    async def create_checkpoint(self, label: str = "") -> dict[str, Any]:
        git = getattr(self._agent, "git", None)
        if git is None:
            return {"ok": False, "error": "Git integration not available"}
        cp = git.create_checkpoint(label)
        if cp is None:
            return {"ok": False, "error": "Failed to create checkpoint"}
        return {"ok": True, "checkpoint": cp}

    async def list_checkpoints(self, n: int = 20) -> dict[str, Any]:
        git = getattr(self._agent, "git", None)
        if git is None:
            return {"ok": False, "error": "Git integration not available", "checkpoints": []}
        return {"ok": True, "checkpoints": git.list_checkpoints(n)}

    async def revert_checkpoint(self, sha: str) -> dict[str, Any]:
        git = getattr(self._agent, "git", None)
        if git is None:
            return {"ok": False, "error": "Git integration not available"}
        ok = git.revert_to_checkpoint(sha)
        return {"ok": ok}

    async def respond_approval(self, req_id: str, approved: bool) -> dict[str, Any]:
        # In-process mode resolves approvals via the shared ApprovalCoordinator
        # Future, so this RPC is never called. Provided for interface parity.
        return {"ok": True}

    @staticmethod
    def _chunk_to_dict(chunk: Any) -> dict[str, Any]:
        return {
            "text": chunk.text,
            "reasoning": chunk.reasoning,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in (chunk.tool_calls or [])
            ],
            "tool_result": chunk.tool_result,
            "done": chunk.done,
            "model": chunk.model,
            "usage": chunk.usage,
            "error": chunk.error,
            "status": chunk.status.value if chunk.status else None,
            "progress_label": chunk.progress_label,
            "advisor_feedback": getattr(chunk, "advisor_feedback", ""),
            "advisor_plan": getattr(chunk, "advisor_plan", ""),
            "finish_reason": chunk.finish_reason,
            "disposition": chunk.disposition.value if getattr(chunk, "disposition", None) else None,
            "evidence": list(getattr(chunk, "evidence", []) or []),
            "missing_evidence": list(getattr(chunk, "missing_evidence", []) or []),
        }

    @staticmethod
    def _event_to_dict(event: Any) -> dict[str, Any]:
        return {
            "phase": event.phase,
            "detail": event.detail,
            "payload": event.payload,
            "progress_label": event.progress_label,
        }


class RemoteTransport(Transport):
    """
    JSON-over-TCP transport to a running DaemonServer.

    Wire protocol — newline-delimited JSON:
      Request:  {"id": "...", "method": "chat_stream", "params": {...}}
      Response: {"id": "...", "type": "data"|"done"|"error", "data": {...}}

    Streaming methods (chat_stream, solve_stream) yield multiple "data"
    responses followed by a "done" response, all sharing the same id.
    """

    def __init__(self, host: str, port: int, headers: dict[str, str] | None = None) -> None:
        self._host = host
        self._port = port
        self._headers = headers or {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._listen_task: asyncio.Task[None] | None = None
        # Unsolicited approval-request frames pushed by the daemon mid-stream
        # are delivered to this callback (set by the ChatComponent).
        self._approval_callback: Any = None
        self.info = TransportInfo(
            kind="remote",
            address=f"{host}:{port}",
            headers=self._headers,
        )

    async def _connect(self) -> None:
        if self._writer is not None:
            return
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                msg = json.loads(line.decode())
                # Unsolicited mid-stream approval request from the daemon.
                # Routed to the TUI; the response travels back over a side
                # connection (see respond_approval) to avoid blocking on the
                # serial per-connection dispatcher.
                if msg.get("type") == "approval_request":
                    cb = self._approval_callback
                    if cb is not None:
                        try:
                            cb(msg.get("data", {}))
                        except Exception as exc:
                            logger.warning("approval callback error: %s", exc)
                    continue
                msg_id = msg.get("id", "")
                queue = self._pending.get(msg_id)
                if queue is not None:
                    await queue.put(msg)
                    if msg.get("type") in ("done", "error"):
                        self._pending.pop(msg_id, None)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            for queue in self._pending.values():
                await queue.put({"type": "error", "data": {"error": "connection closed"}})
            self._pending.clear()

    def set_approval_callback(self, callback: Any) -> None:
        """Register the TUI-side receiver for daemon-pushed approval requests."""
        self._approval_callback = callback

    async def _send(self, method: str, params: dict[str, Any]) -> str:
        await self._connect()
        assert self._writer is not None
        msg_id = uuid.uuid4().hex[:12]
        self._pending[msg_id] = asyncio.Queue()
        line = json.dumps({"id": msg_id, "method": method, "params": params})
        self._writer.write((line + "\n").encode())
        await self._writer.drain()
        return msg_id

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        msg_id = await self._send(method, params)
        queue = self._pending[msg_id]
        msg = await queue.get()
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("data", {}).get("error", "unknown error"))
        return msg.get("data", {})

    async def _stream_request(
        self, method: str, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        msg_id = await self._send(method, params)
        queue = self._pending[msg_id]
        while True:
            msg = await queue.get()
            msg_type = msg.get("type")
            if msg_type == "data":
                yield msg.get("data", {})
            elif msg_type == "error":
                raise RuntimeError(msg.get("data", {}).get("error", "unknown error"))
            elif msg_type == "done":
                break

    async def chat_stream(self, message: str) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self._stream_request("chat_stream", {"message": message}):
            yield chunk

    async def solve_stream(self, task: str) -> AsyncIterator[dict[str, Any]]:
        async for event in self._stream_request("solve_stream", {"task": task}):
            yield event

    async def submit_feedback(self, feedback: str) -> dict[str, Any]:
        return await self._request("submit_feedback", {"feedback": feedback})

    async def get_status(self) -> dict[str, Any]:
        return await self._request("get_status", {})

    async def set_tier(self, name: str) -> dict[str, Any]:
        return await self._request("set_tier", {"name": name})

    async def set_reasoning_effort(self, effort: str) -> dict[str, Any]:
        return await self._request("set_reasoning_effort", {"effort": effort})

    async def set_nitro_mode(self, enabled: bool) -> dict[str, Any]:
        return await self._request("set_nitro_mode", {"enabled": enabled})

    async def set_fast_mode(self, enabled: bool) -> dict[str, Any]:
        return await self._request("set_fast_mode", {"enabled": enabled})

    async def reset_context(self) -> dict[str, Any]:
        return await self._request("reset_context", {})

    async def compact_context(self) -> dict[str, Any]:
        return await self._request("compact_context", {})

    async def list_sessions(self) -> dict[str, Any]:
        return await self._request("list_sessions", {})

    async def resume_session(self, session_id: str) -> dict[str, Any]:
        return await self._request("resume_session", {"session_id": session_id})

    async def list_models(self, refresh: bool = False) -> dict[str, Any]:
        return await self._request("list_models", {"refresh": refresh})

    async def set_model(self, role: str, profile_id: str, model_id: str) -> dict[str, Any]:
        return await self._request("set_model", {"role": role, "profile_id": profile_id, "model_id": model_id})

    async def create_checkpoint(self, label: str = "") -> dict[str, Any]:
        return await self._request("create_checkpoint", {"label": label})

    async def list_checkpoints(self, n: int = 20) -> dict[str, Any]:
        return await self._request("list_checkpoints", {"n": n})

    async def revert_checkpoint(self, sha: str) -> dict[str, Any]:
        return await self._request("revert_checkpoint", {"sha": sha})

    async def respond_approval(self, req_id: str, approved: bool) -> dict[str, Any]:
        # Send the decision over a FRESH side connection. The main connection
        # is blocked inside the streaming chat RPC whose read loop is busy, so
        # a request on it would deadlock; a one-shot connection is dispatched
        # immediately by the server and resolves the daemon-side Future.
        try:
            reader, writer = await asyncio.open_connection(self._host, self._port)
        except OSError as exc:
            raise RuntimeError(f"could not open approval side connection: {exc}") from exc
        try:
            msg_id = uuid.uuid4().hex[:12]
            line = json.dumps({
                "id": msg_id,
                "method": "respond_approval",
                "params": {"id": req_id, "approved": approved},
            })
            writer.write((line + "\n").encode())
            await writer.drain()
            resp_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not resp_line:
                return {"ok": False}
            resp = json.loads(resp_line.decode())
            return resp.get("data", {})
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (asyncio.CancelledError, Exception):
                pass

    async def close(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except asyncio.CancelledError:
                pass
        self._writer = None
        self._reader = None
