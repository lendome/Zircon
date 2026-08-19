"""
Daemon service — lifecycle management for the background daemon process.

Handles starting, stopping, and querying the daemon. The daemon writes
a PID file + port file to the .zircon-code/ directory so the CLI can
discover and manage it across process boundaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.cli.daemon.service")

_DAEMON_LOCK_NAME = "daemon.lock"


@dataclass
class DaemonInfo:
    """Info about a running daemon, read from the lock file."""

    pid: int
    port: int
    host: str

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> DaemonInfo:
        return DaemonInfo(pid=d["pid"], port=d["port"], host=d.get("host", "127.0.0.1"))


def _lock_path(workspace: str) -> Path:
    return Path(workspace) / ".zircon-code" / _DAEMON_LOCK_NAME


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class DaemonService:
    """
    Manages the daemon lifecycle.

    - start(): spawn a background process running the daemon server
    - stop(): kill the background process
    - info(): read the lock file for the running daemon
    - is_running(): check if the daemon process is alive
    - transport(): get a RemoteTransport connected to the running daemon,
                    or None if not running
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = str(Path(workspace).resolve())
        self._lock = _lock_path(self.workspace)

    def info(self) -> DaemonInfo | None:
        if not self._lock.exists():
            return None
        try:
            data = json.loads(self._lock.read_text())
            return DaemonInfo.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def is_running(self) -> bool:
        info = self.info()
        if info is None:
            return False
        if not self._pid_alive(info.pid):
            self._lock.unlink(missing_ok=True)
            return False
        return self._port_open(info.host, info.port)

    def start(self, port: int = 0, host: str = "127.0.0.1") -> DaemonInfo:
        """Start the daemon as a background process."""
        if self.is_running():
            existing = self.info()
            assert existing is not None
            return existing

        import subprocess

        _ensure_dir(self._lock)
        log_path = self._lock.parent / "daemon.log"
        log_file = open(log_path, "a", encoding="utf-8")

        cmd = [
            sys.executable, "-m", "zirconAgent.cli",
            "serve",
            "--host", host,
            "--port", str(port),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            cwd=self.workspace,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        logger.info("Started daemon process pid=%d", proc.pid)

        for _ in range(50):
            info = self.info()
            if info and info.pid == proc.pid:
                return info
            asyncio.run(asyncio.sleep(0.1))

        if proc.poll() is not None:
            raise RuntimeError("Daemon process exited immediately — check daemon.log")
        raise RuntimeError("Daemon did not write lock file in time")

    def stop(self) -> bool:
        """Stop the running daemon. Returns True if it was stopped."""
        info = self.info()
        if info is None:
            return False
        if not self._pid_alive(info.pid):
            self._lock.unlink(missing_ok=True)
            return False

        try:
            if sys.platform == "win32":
                os.kill(info.pid, signal.SIGTERM)
            else:
                os.kill(info.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._lock.unlink(missing_ok=True)
            return False

        for _ in range(20):
            if not self._pid_alive(info.pid):
                break
            asyncio.run(asyncio.sleep(0.1))

        self._lock.unlink(missing_ok=True)
        return True

    def restart(self, port: int = 0, host: str = "127.0.0.1") -> DaemonInfo:
        self.stop()
        return self.start(port=port, host=host)

    async def transport(self) -> Any:
        """Get a RemoteTransport to the running daemon, or None."""
        from .transport import RemoteTransport

        if not self.is_running():
            return None
        info = self.info()
        if info is None:
            return None
        return RemoteTransport(info.host, info.port)

    def ensure_running(self, port: int = 0, host: str = "127.0.0.1") -> DaemonInfo:
        """Ensure a daemon is running, starting one if needed."""
        if self.is_running():
            existing = self.info()
            assert existing is not None
            return existing
        return self.start(port=port, host=host)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    def write_lock(self, info: DaemonInfo) -> None:
        _ensure_dir(self._lock)
        self._lock.write_text(json.dumps({
            "pid": info.pid,
            "port": info.port,
            "host": info.host,
        }))

    def clear_lock(self) -> None:
        self._lock.unlink(missing_ok=True)
