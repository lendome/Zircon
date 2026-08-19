"""Sandboxed execution — Docker-based isolation for running untrusted code/tests.

This module implements "Sandboxed Execution" from the blueprint:

1. Docker container management (start/stop/reset)
2. Command execution inside containers with timeouts
3. File system mounts for test execution
4. Container lifecycle tied to agent sessions

The sandbox ensures that running tests or arbitrary commands doesn't:
- Corrupt the host filesystem
- Install malicious packages
- Delete system files
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import TierConfig

logger = logging.getLogger("agent.core.sandbox_executor")


@dataclass
class SandboxResult:
    """Result of executing a command in the sandbox."""

    success: bool
    output: str
    return_code: int = -1
    duration: float = 0.0
    sandbox_id: str = ""
    timed_out: bool = False


@dataclass
class SandboxConfig:
    """Configuration for the sandbox environment."""

    enabled: bool = False
    """Master switch — sandboxing is opt-in."""

    image: str = "python:3.11-slim"
    """Docker image to use for the sandbox."""

    timeout: int = 60
    """Default timeout for sandboxed commands (seconds)."""

    memory_limit: str = "1g"
    """Memory limit for the container."""

    cpu_limit: float = 1.0
    """CPU limit for the container (cores)."""

    network_enabled: bool = False
    """Whether the sandbox has network access. Disabled by default for safety."""

    mount_workspace: bool = True
    """Whether to mount the workspace directory into the sandbox."""

    max_concurrent: int = 2
    """Maximum number of concurrent sandbox containers."""


class SandboxExecutor:
    """Manages Docker containers for sandboxed execution.

    Provides:
    - Container lifecycle (create, exec, remove)
    - Isolated command execution with timeouts
    - Workspace mounting for accessing project files
    - Clean resource management
    """

    def __init__(
        self,
        repo_path: str | Path,
        config: SandboxConfig | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.config = config or SandboxConfig()
        self._containers: dict[str, str] = {}  # session_id -> container_id
        self._active_count: int = 0
        self._available: bool | None = None  # None = not checked yet

    async def check_available(self) -> bool:
        """Check if Docker is available on this system."""
        if self._available is not None:
            return self._available

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._available = proc.returncode == 0
            if self._available:
                logger.info("Docker is available for sandboxed execution")
            else:
                logger.warning("Docker is not available — sandbox disabled")
            return self._available
        except FileNotFoundError:
            self._available = False
            logger.warning("Docker not found — sandbox disabled")
            return False

    async def start_session(self, session_id: str) -> bool:
        """Start a sandbox container for a new agent session.

        Returns True if the container was started successfully.
        """
        if not self.config.enabled:
            logger.debug("Sandbox is disabled, skipping container start")
            return False

        if not await self.check_available():
            return False

        if self._active_count >= self.config.max_concurrent:
            logger.warning("Max concurrent sandboxes reached (%d)", self.config.max_concurrent)
            return False

        try:
            container_id = await self._create_container(session_id)
            if container_id:
                self._containers[session_id] = container_id
                self._active_count += 1
                logger.info("Sandbox container started: %s (session=%s)", container_id[:12], session_id)
                return True
            return False
        except Exception as e:
            logger.warning("Failed to create sandbox: %s", e)
            return False

    async def execute(
        self,
        command: str,
        session_id: str = "",
        timeout: int | None = None,
        workdir: str | None = None,
    ) -> SandboxResult:
        """Execute a command inside the sandbox container.

        Args:
            command: Shell command to execute
            session_id: Session ID (used to find the right container)
            timeout: Execution timeout in seconds
            workdir: Working directory inside the container

        Returns:
            SandboxResult with output and status
        """
        if not self.config.enabled:
            return SandboxResult(
                success=False,
                output="Sandbox is disabled. Set enabled: true in sandbox config.",
                return_code=-1,
            )

        if not await self.check_available():
            return SandboxResult(
                success=False,
                output="Docker is not available. Install Docker to use sandbox.",
                return_code=-1,
            )

        container_id = self._containers.get(session_id, "")
        if not container_id:
            return SandboxResult(
                success=False,
                output=f"No sandbox container for session '{session_id}'. Call start_session() first.",
                return_code=-1,
            )

        timeout = timeout or self.config.timeout
        t0 = time.monotonic()

        try:
            cmd = ["docker", "exec"]
            if workdir:
                cmd.extend(["-w", workdir])
            cmd.extend([container_id, "/bin/sh", "-c", command])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration = time.monotonic() - t0
                return SandboxResult(
                    success=False,
                    output=f"Command timed out after {timeout}s",
                    return_code=-1,
                    duration=duration,
                    sandbox_id=container_id[:12],
                    timed_out=True,
                )

            duration = time.monotonic() - t0
            output = (stdout.decode("utf-8", errors="replace") +
                     stderr.decode("utf-8", errors="replace")).strip()

            return SandboxResult(
                success=proc.returncode == 0,
                output=output,
                return_code=proc.returncode or 0,
                duration=duration,
                sandbox_id=container_id[:12],
            )

        except Exception as e:
            return SandboxResult(
                success=False,
                output=f"Sandbox execution error: {e}",
                return_code=-1,
                duration=time.monotonic() - t0,
            )

    async def run_tests(
        self,
        test_command: str,
        session_id: str = "",
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run tests inside the sandbox.

        This is a convenience wrapper around execute() that sets the
        working directory to the mounted workspace.
        """
        result = await self.execute(
            command=test_command,
            session_id=session_id,
            timeout=timeout,
            workdir="/workspace",
        )
        return result

    async def stop_session(self, session_id: str) -> bool:
        """Stop and remove the sandbox container for a session."""
        container_id = self._containers.pop(session_id, "")
        if not container_id:
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            self._active_count -= 1
            logger.info("Sandbox container removed: %s (session=%s)", container_id[:12], session_id)
            return proc.returncode == 0
        except Exception as e:
            logger.warning("Failed to remove sandbox container: %s", e)
            return False

    async def stop_all(self) -> None:
        """Stop all active sandbox containers."""
        session_ids = list(self._containers.keys())
        for sid in session_ids:
            await self.stop_session(sid)

    async def _create_container(self, session_id: str) -> str | None:
        """Create a sandbox Docker container.

        Returns the container ID, or None on failure.
        """
        image = self.config.image
        container_name = f"zircon-sandbox-{session_id[:16]}"

        cmd = [
            "docker", "run", "-d",
            "--rm",
            "--name", container_name,
            "--memory", self.config.memory_limit,
            "--cpus", str(self.config.cpu_limit),
        ]

        if not self.config.network_enabled:
            cmd.append("--network")
            cmd.append("none")

        if self.config.mount_workspace:
            cmd.extend(["-v", f"{self.repo_path}:/workspace"])

        cmd.append(image)
        cmd.append("tail")
        cmd.append("-f")
        cmd.append("/dev/null")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                container_id = stdout.decode().strip()
                return container_id
            else:
                logger.warning("Failed to create sandbox: %s", stderr.decode())
                return None
        except asyncio.TimeoutError:
            logger.warning("Docker container creation timed out")
            return None
        except Exception as e:
            logger.warning("Docker container creation error: %s", e)
            return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop_all()