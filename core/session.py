from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import ZIRCON_DIR, ensure_zircon_dir
from .types import TaskStatus

logger = logging.getLogger("agent.core.session")

# Resumed sessions must fit alongside a new request and system prompt. The
# canonical transcript remains exact; only the model replay projection is
# bounded here.
_RESUMED_TOOL_RESULT_MAX_CHARS = 12_000
_SESSION_SCHEMA_VERSION = 2


@dataclass
class Session:
    id: str = ""
    task: str = ""
    status: str = "created"
    started_at: str = ""
    finished_at: str = ""
    files_modified: list[str] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]


class AdmissionConflictError(ValueError):
    """Raised when a caller reuses an admission ID for different work."""


@dataclass(frozen=True)
class PromptAdmission:
    """Durably admitted input awaiting promotion into the live transcript."""

    id: str
    sequence: int
    content: str
    delivery: str
    status: str = "pending"


class SessionManager:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        ensure_zircon_dir(self.repo_path)
        self.session_dir = self.repo_path / ZIRCON_DIR / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current: Session | None = None
        self._drain_active = False

    @property
    def current(self) -> Session | None:
        return self._current

    def start(self, task: str) -> Session:
        # Auto-close previous orphaned session before starting a new one
        if self._current is not None and self._current.finished_at == "":
            logger = logging.getLogger("agent.core.session")
            logger.warning("Auto-closing orphaned session %s before starting new one", self._current.id)
            self._current.finished_at = datetime.now(timezone.utc).isoformat()
            self._current.status = TaskStatus.COMPLETED
            self._write_manifest(self._current)

        session = Session(
            task=task,
            status=TaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._current = session
        session_path = self.session_dir / session.id
        session_path.mkdir(exist_ok=True)
        self._write_manifest(session)
        self._init_journal(session)
        return session

    def begin_drain(self) -> bool:
        """Claim this manager's live execution slot.

        The lease is intentionally process-local. Durable inbox records make
        accepted work recoverable, while the lease prevents concurrent callers
        from running overlapping model/tool loops in this process.
        """
        if self._drain_active:
            return False
        self._drain_active = True
        return True

    def end_drain(self) -> None:
        self._drain_active = False

    @property
    def drain_active(self) -> bool:
        return self._drain_active

    def admit_prompt(
        self,
        content: str,
        admission_id: str | None = None,
        delivery: str = "queue",
    ) -> PromptAdmission:
        """Persist a prompt before execution and reconcile exact retries.

        `admission_id` may be supplied by a caller retrying an RPC request.
        Reuse is accepted only when the content and delivery mode are exactly
        the same; a divergent reuse is a conflict rather than duplicate work.
        """
        if not self._current:
            raise RuntimeError("Cannot admit a prompt without an active session")
        if delivery not in {"queue", "steer"}:
            raise ValueError("delivery must be 'queue' or 'steer'")

        inbox = self._load_inbox()
        prompt_id = admission_id or uuid.uuid4().hex
        for item in inbox:
            if item.get("id") != prompt_id:
                continue
            if item.get("content") == content and item.get("delivery") == delivery:
                return self._as_admission(item)
            raise AdmissionConflictError(
                f"Admission ID '{prompt_id}' is already associated with different input"
            )

        sequence = max((int(item.get("sequence", 0)) for item in inbox), default=0) + 1
        item = {
            "id": prompt_id,
            "sequence": sequence,
            "content": content,
            "delivery": delivery,
            "status": "pending",
            "admitted_at": datetime.now(timezone.utc).isoformat(),
        }
        inbox.append(item)
        self._save_inbox(inbox)
        self.append_journal("prompt_admitted", {
            "id": prompt_id,
            "sequence": sequence,
            "delivery": delivery,
        })
        return self._as_admission(item)

    def promote_prompts(self, include_queued: bool = False) -> list[PromptAdmission]:
        """Promote admitted work in deterministic order at a turn boundary.

        Steering inputs are promoted together in their admission order. Queued
        work is promoted only when the caller is otherwise idle, one item at a
        time, so bursts do not become nested or competing provider turns.
        """
        if not self._current:
            return []
        inbox = self._load_inbox()
        pending = sorted(
            (item for item in inbox if item.get("status") == "pending"),
            key=lambda item: int(item.get("sequence", 0)),
        )
        selected = [item for item in pending if item.get("delivery") == "steer"]
        if not selected and include_queued:
            queued = next((item for item in pending if item.get("delivery") == "queue"), None)
            if queued:
                selected = [queued]

        for item in selected:
            item["status"] = "promoted"
            item["promoted_at"] = datetime.now(timezone.utc).isoformat()
        if selected:
            self._save_inbox(inbox)
            for item in selected:
                self.append_journal("prompt_promoted", {
                    "id": item["id"],
                    "sequence": item["sequence"],
                    "delivery": item["delivery"],
                })
        return [self._as_admission(item) for item in selected]

    def settle_prompt(self, admission_id: str, status: str = "consumed") -> bool:
        """Mark a promoted prompt settled after it becomes model-visible."""
        if not self._current:
            return False
        inbox = self._load_inbox()
        for item in inbox:
            if item.get("id") == admission_id:
                item["status"] = status
                item["settled_at"] = datetime.now(timezone.utc).isoformat()
                self._save_inbox(inbox)
                self.append_journal("prompt_settled", {
                    "id": admission_id,
                    "status": status,
                })
                return True
        return False

    def list_admissions(self) -> list[PromptAdmission]:
        return [self._as_admission(item) for item in self._load_inbox()]

    def append_journal(self, event_type: str, payload: Any = None) -> None:
        if not self._current:
            return
        journal_path = self.session_dir / self._current.id / "journal.jsonl"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": payload,
        }
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def set_status(self, status: TaskStatus) -> None:
        if not self._current:
            return
        self._current.status = status
        self._write_manifest(self._current)

    def close(self, status: TaskStatus = TaskStatus.COMPLETED, tokens_used: int = 0) -> None:
        if not self._current:
            return
        self._current.status = status
        self._current.finished_at = datetime.now(timezone.utc).isoformat()
        self._current.tokens_used = tokens_used
        self._write_manifest(self._current)

    def add_cost(self, cost_usd: float) -> None:
        """Accumulate a provider-reported request cost for the active session."""
        if not self._current:
            return
        try:
            cost = float(cost_usd)
        except (TypeError, ValueError):
            return
        if cost <= 0:
            return
        self._current.cost_usd += cost
        self._write_manifest(self._current)

    def track_file(self, path: str) -> None:
        if self._current and path not in self._current.files_modified:
            self._current.files_modified.append(path)

    def save_messages(self, messages: list[dict]) -> None:
        """Atomically save the canonical transcript for the current session."""
        if not self._current:
            return
        messages_path = self.session_dir / self._current.id / "messages.json"
        messages_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = messages_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(messages, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(temporary_path, messages_path)

    def append_messages(self, messages: list[dict]) -> None:
        """Append messages to the active session's canonical transcript."""
        if not self._current or not messages:
            return
        existing = self.load_messages(self._current.id)
        self.save_messages([*existing, *(dict(message) for message in messages)])

    def load_messages(self, session_id: str) -> list[dict]:
        """Load the full chat messages for a given session from disk.

        Returns messages in the persisted schema (``{type, text}``); use
        :meth:`to_history_messages` to convert them into the ``{role, content}``
        shape the LLM context expects.
        """
        session_path = self._safe_session_path(session_id)
        if session_path is None:
            return []
        messages_path = session_path / "messages.json"
        if not messages_path.exists():
            return []
        try:
            return json.loads(messages_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def to_history_messages(messages: list[dict]) -> list[dict]:
        """Convert persisted ``{type, text}`` messages into ``{role, content}``.

        The save path (:meth:`save_messages`, fed by the agent) stores a
        compact ``{type: "user"|"text"|"tool_result", text}`` schema. The
        agent's ``context.history`` and the LLM router expect the OpenAI-style
        ``{role, content}`` shape, so a resumed session must be converted back
        before being assigned to ``context.history``. Messages already in the
        ``{role, content}`` shape pass through unchanged.
        """
        result: list[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if "role" in msg:
                restored = dict(msg)
                if restored.get("role") == "tool":
                    content = restored.get("content")
                    if isinstance(content, str) and len(content) > _RESUMED_TOOL_RESULT_MAX_CHARS:
                        omitted = len(content) - _RESUMED_TOOL_RESULT_MAX_CHARS
                        head_size = _RESUMED_TOOL_RESULT_MAX_CHARS * 2 // 3
                        tail_size = _RESUMED_TOOL_RESULT_MAX_CHARS - head_size
                        restored["content"] = (
                            content[:head_size]
                            + f"\n... [session resume: {omitted} chars omitted] ...\n"
                            + content[-tail_size:]
                        )
                result.append(restored)
                continue
            mtype = msg.get("type", "")
            text = msg.get("text", "")
            if mtype == "user":
                result.append({"role": "user", "content": text})
            elif mtype == "text":
                result.append({"role": "assistant", "content": text})
            elif mtype == "tool_result":
                # Legacy entries have no tool_call_id and no matching
                # assistant tool_calls — emitting them as role="tool"
                # orphans them and providers reject the request (400),
                # killing the resumed session. Keep the content as a
                # user-role context note instead.
                result.append({"role": "user", "content": f"[prior tool output]\n{text}"})
        return result

    def _write_manifest(self, session: Session) -> None:
        manifest_path = self.session_dir / session.id / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": _SESSION_SCHEMA_VERSION,
            "id": session.id,
            "task": session.task,
            "status": session.status,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "files_modified": session.files_modified,
            "tokens_used": session.tokens_used,
            "cost_usd": session.cost_usd,
        }
        manifest_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _init_journal(self, session: Session) -> None:
        journal_path = self.session_dir / session.id / "journal.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "session_start",
            "payload": {"task": session.task},
        }
        journal_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    def _inbox_path(self) -> Path:
        if not self._current:
            raise RuntimeError("No active session")
        return self.session_dir / self._current.id / "inbox.json"

    def _load_inbox(self) -> list[dict[str, Any]]:
        if not self._current:
            return []
        path = self._inbox_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_inbox(self, inbox: list[dict[str, Any]]) -> None:
        path = self._inbox_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(inbox, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _as_admission(item: dict[str, Any]) -> PromptAdmission:
        return PromptAdmission(
            id=str(item["id"]),
            sequence=int(item["sequence"]),
            content=str(item["content"]),
            delivery=str(item["delivery"]),
            status=str(item.get("status", "pending")),
        )

    def list_sessions(self) -> list[dict]:
        sessions = []
        if not self.session_dir.exists():
            return sessions
        for d in self.session_dir.iterdir():
            manifest = d / "manifest.json"
            if manifest.exists():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    sessions.append(data)
                except Exception:
                    pass
        return sorted(
            sessions,
            key=lambda session: (
                bool(self._current and session.get("id") == self._current.id),
                session.get("finished_at") or session.get("started_at") or "",
            ),
            reverse=True,
        )

    def load_session(self, session_id: str) -> Session | None:
        """Load a persisted session as the active session without mutating it."""
        session_path = self._safe_session_path(session_id)
        if session_path is None:
            return None
        manifest = session_path / "manifest.json"
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            session = Session(
                id=str(data.get("id", session_id)),
                task=str(data.get("task", "")),
                status=str(data.get("status", "created")),
                started_at=str(data.get("started_at", "")),
                finished_at=str(data.get("finished_at", "")),
                files_modified=list(data.get("files_modified", [])),
                tokens_used=int(data.get("tokens_used", 0)),
                cost_usd=float(data.get("cost_usd", 0.0)),
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        self._current = session
        return session

    def reopen(self) -> None:
        """Mark the active session running so new turns append to it."""
        if not self._current:
            return
        self._current.status = TaskStatus.RUNNING
        self._current.finished_at = ""
        self._write_manifest(self._current)

    def _safe_session_path(self, session_id: str) -> Path | None:
        """Resolve a session path without allowing traversal outside storage."""
        if not session_id or Path(session_id).name != session_id:
            return None
        candidate = (self.session_dir / session_id).resolve()
        try:
            candidate.relative_to(self.session_dir.resolve())
        except ValueError:
            return None
        return candidate
