"""
Daemon layer — backend server, lifecycle management, and transport abstraction.

The TUI never runs business logic directly. It talks to a backend daemon
over a transport (JSON-over-TCP or in-process). This separation means the
TUI can crash or be restarted without losing sessions.
"""

from __future__ import annotations

from .transport import Transport, LocalTransport, RemoteTransport
from .service import DaemonService
from .server import DaemonServer

__all__ = [
    "Transport",
    "LocalTransport",
    "RemoteTransport",
    "DaemonService",
    "DaemonServer",
]
