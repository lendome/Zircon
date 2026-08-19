"""
Sync provider — the central reactive data store for backend state.

Holds all backend state in a single reactive store, populated by an event
stream from the backend. Three-phase loading: loading → partial → complete.

  loading:  nothing ready, show loading screen
  partial:  sessions list loaded, can navigate
  complete: all data synced, full functionality
"""

from __future__ import annotations

from typing import Any, Callable

from ..context import Context, ContextRegistry
from ..reactive.store import Store, create_store
from .base import Provider


SYNC_INITIAL: dict[str, Any] = {
    "status": "loading",         # loading | partial | complete
    "provider": [],
    "provider_default": {},
    "agent": [],
    "command": [],
    "session": [],
    "session_status": {},         # sessionID -> status
    "session_diff": {},           # sessionID -> file diffs
    "message": {},                # sessionID -> [Message]
    "part": {},                   # messageID -> [Part]
    "todo": {},                   # sessionID -> [Todo]
    "permission": {},             # sessionID -> [PermissionRequest]
    "question": {},               # sessionID -> [QuestionRequest]
    "config": {},
    "lsp": [],
    "mcp": {},
    "mcp_resource": {},
    "formatter": [],
    "vcs": None,
    "console_state": {},
}


class SyncProvider(Provider):
    name = "sync"

    def provide(self, registry: ContextRegistry) -> Any:
        store = create_store(SYNC_INITIAL)
        ctx = Context(name=self.name)
        ctx.set(store)
        registry.register(ctx)
        return store
