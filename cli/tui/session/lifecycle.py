"""
Session lifecycle — list, create, fork, share, compact, rename, delete.

These are the core session operations. They communicate with the backend
through the transport (SDK). The TUI calls these methods and the backend
(agent/daemon) executes them.
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionInfo:
    """Metadata about a session."""

    id: str = ""
    title: str = ""
    parent_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    share_url: str | None = None
    directory: str = ""
    agent: str = ""
    status: str = "created"
    files_modified: int = 0
    is_active: bool = False

    @property
    def is_child(self) -> bool:
        return self.parent_id is not None

    @property
    def is_default_title(self) -> bool:
        return not self.title or self.title.startswith("Session ")


class SessionLifecycle:
    """
    Core session lifecycle operations.

    All operations go through the transport (SDK) to the backend.
    The backend (agent/daemon) persists sessions to disk.
    """

    def __init__(self, transport: Any, workspace: str = ".") -> None:
        self._transport = transport
        self._workspace = workspace
        self._sessions: list[SessionInfo] = []
        self._resumed_messages: list[dict] = []

    @property
    def sessions(self) -> list[SessionInfo]:
        return list(self._sessions)

    @property
    def resumed_messages(self) -> list[dict]:
        """Persisted chat messages from the most recent :meth:`resume` call."""
        return list(self._resumed_messages)

    async def refresh_sessions(self) -> list[SessionInfo]:
        """Refresh the session list from the backend."""
        result = await self._transport.list_sessions()
        active_id = str(result.get("active_id", ""))
        self._sessions = [
            self._from_backend(item, active_id=active_id)
            for item in result.get("sessions", [])
        ]
        return self.sessions

    async def resume(self, session_id: str) -> SessionInfo | None:
        """Make a persisted session active in the current agent.

        On success the persisted chat messages are captured in
        :attr:`resumed_messages` so the caller (TUI) can replay them into the
        visible transcript — the backend only restores them into the agent's
        LLM context, not the UI.
        """
        result = await self._transport.resume_session(session_id)
        if not result.get("ok"):
            self._resumed_messages = []
            return None
        session = self._from_backend(result["session"])
        for item in self._sessions:
            item.is_active = False
        session.is_active = True
        self._sessions = [item for item in self._sessions if item.id != session.id]
        self._sessions.append(session)
        self._resumed_messages = result.get("messages") or []
        return session

    async def continue_last(self) -> SessionInfo | None:
        """Continue the most recent session (for -c flag)."""
        if not self._sessions:
            return None
        # Sort by updated_at descending, find top-level session
        sorted_sessions = sorted(
            [s for s in self._sessions if s.parent_id is None],
            key=lambda s: s.updated_at,
            reverse=True,
        )
        return sorted_sessions[0] if sorted_sessions else None

    async def fork(self, session_id: str) -> SessionInfo | None:
        """Fork an existing session.

        Creates a new session that branches from the given message.
        """
        return None

    async def share(self, session_id: str) -> str | None:
        """Share a session. Returns the share URL."""
        # First time: ask for consent (handled by caller via DialogConfirm)
        # Then: sdk.session.share({ sessionID })
        # Returns: result.data.share.url
        return None  # stub — backend would implement this

    async def unshare(self, session_id: str) -> bool:
        """Stop sharing a session."""
        return True  # stub

    async def compact(self, session_id: str, model: str = "", provider: str = "") -> bool:
        """Compact/summarize a session to free context window."""
        try:
            return bool((await self._transport.compact_context()).get("ok"))
        except Exception:
            return False

    async def rename(self, session_id: str, title: str) -> bool:
        """Rename a session."""
        for s in self._sessions:
            if s.id == session_id:
                s.title = title
                s.updated_at = time.time()
                return True
        return False

    async def delete(self, session_id: str) -> bool:
        """Delete a session."""
        self._sessions = [s for s in self._sessions if s.id != session_id]
        return True

    async def get(self, session_id: str) -> SessionInfo | None:
        """Get a session by ID."""
        for s in self._sessions:
            if s.id == session_id:
                return s
        return None

    def filter_by_directory(self, directory: str) -> list[SessionInfo]:
        """Filter sessions to only show those for the given directory."""
        return [s for s in self._sessions if s.directory == directory]

    def _from_backend(self, item: dict[str, Any], active_id: str = "") -> SessionInfo:
        started_at = self._timestamp(item.get("started_at", ""))
        finished_at = self._timestamp(item.get("finished_at", ""))
        return SessionInfo(
            id=str(item.get("id", "")),
            title=str(item.get("task", "Untitled session")),
            created_at=started_at,
            updated_at=finished_at or started_at,
            directory=self._workspace,
            agent="Zircon",
            status=str(item.get("status", "created")),
            files_modified=len(item.get("files_modified", []) or []),
            is_active=str(item.get("id", "")) == active_id,
        )

    @staticmethod
    def _timestamp(value: Any) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
