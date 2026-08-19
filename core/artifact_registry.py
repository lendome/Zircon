from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import zircon_path

logger = logging.getLogger("agent.artifact_registry")


class ArtifactRegistry:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self._lock = asyncio.Lock()
        self._artifacts: dict[str, dict[str, Any]] = {}
        self._consumed: dict[str, set[str]] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}
        self._version_counter: int = 0


    async def publish(
        self,
        agent_id: str,
        artifact_key: str,
        artifact_type: str,
        data: Any,
        max_tokens: int = 10000,
    ) -> None:
        async with self._lock:
            self._version_counter += 1
            data_str = json.dumps(data, default=str, ensure_ascii=False)
            if len(data_str) > max_tokens * 4:
                truncated = data_str[: max_tokens * 4]
                data = json.loads(truncated) if truncated.startswith(("{", "[")) else {"_truncated": True, "preview": truncated[:500]}
                logger.warning("artifact %s truncated to %d chars", artifact_key, max_tokens * 4)

            self._artifacts[artifact_key] = {
                "producer_id": agent_id,
                "artifact_type": artifact_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": self._version_counter,
            }
            logger.info(
                "artifact published: agent=%s key=%s type=%s v=%d",
                agent_id, artifact_key, artifact_type, self._version_counter,
            )

            for event in self._waiters.pop(artifact_type, []):
                event.set()

            for event in self._waiters.pop(artifact_key, []):
                event.set()

    async def get(self, artifact_key: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._artifacts.get(artifact_key)

    async def consume(self, agent_id: str, artifact_key: str) -> dict[str, Any] | None:
        async with self._lock:
            data = self._artifacts.get(artifact_key)
            if data is not None:
                self._consumed.setdefault(agent_id, set()).add(artifact_key)
            return data

    async def list_by_type(self, artifact_type: str) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                {"key": k, **v}
                for k, v in self._artifacts.items()
                if v["artifact_type"] == artifact_type
            ]

    async def list_all(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            return dict(self._artifacts)

    async def wait_for(self, artifact_type: str, timeout: float = 60.0) -> dict[str, Any] | None:
        async with self._lock:
            for key, val in self._artifacts.items():
                if val["artifact_type"] == artifact_type:
                    return {"key": key, **val}

            event = asyncio.Event()
            self._waiters.setdefault(artifact_type, []).append(event)

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        async with self._lock:
            for key, val in self._artifacts.items():
                if val["artifact_type"] == artifact_type:
                    return {"key": key, **val}
        return None

    async def agent_progress(self, agent_id: str) -> dict[str, Any]:
        async with self._lock:
            produced = [
                {"key": k, **v}
                for k, v in self._artifacts.items()
                if v["producer_id"] == agent_id
            ]
            consumed = list(self._consumed.get(agent_id, set()))
            return {
                "agent_id": agent_id,
                "produced": produced,
                "consumed": consumed,
            }

    async def reset(self) -> None:
        async with self._lock:
            self._artifacts.clear()
            self._consumed.clear()
            self._waiters.clear()
            self._version_counter = 0


    def snapshot_path(self) -> Path:
        return zircon_path(self.repo_path, "swarm", "registry_snapshot.json")

    async def save_snapshot(self) -> None:
        async with self._lock:
            data = {
                "artifacts": self._artifacts,
                "consumed": {k: list(v) for k, v in self._consumed.items()},
                "version": self._version_counter,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        path = self.snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.debug("artifact registry snapshot saved (%d artifacts)", len(self._artifacts))

    async def load_snapshot(self) -> bool:
        path = self.snapshot_path()
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            async with self._lock:
                self._artifacts = data.get("artifacts", {})
                self._consumed = {k: set(v) for k, v in data.get("consumed", {}).items()}
                self._version_counter = data.get("version", 0)
            logger.info("artifact registry snapshot loaded (%d artifacts)", len(self._artifacts))
            return True
        except Exception as e:
            logger.warning("failed to load artifact snapshot: %s", e)
            return False