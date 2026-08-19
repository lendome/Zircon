from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from .constants import ZIRCON_DIR, ensure_zircon_dir, zircon_path

logger = logging.getLogger("agent.core.task_manager")

_running_tasks: dict[str, "BackgroundTask"] = {}


@dataclass
class BackgroundTask:
    id: str
    name: str
    status: str = "pending"  # pending, running, completed, failed
    created_at: float = 0.0
    finished_at: float = 0.0
    error: str | None = None
    result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "metadata": self.metadata,
        }


def create_background_task(
    repo_path: str | Path,
    coro: Coroutine[Any, Any, Any],
    name: str = "",
    metadata: dict[str, Any] | None = None,
) -> BackgroundTask:
    task_id = f"bg_{uuid.uuid4().hex[:12]}"
    task = BackgroundTask(
        id=task_id,
        name=name or coro.__name__,
        created_at=time.time(),
        metadata=metadata or {},
    )
    _running_tasks[task_id] = task
    _persist_task(repo_path, task)

    async def _wrapped():
        try:
            task.status = "running"
            _persist_task(repo_path, task)
            result = await coro
            task.result = str(result)[:2000] if result else None
            task.status = "completed"
        except asyncio.CancelledError:
            task.status = "failed"
            task.error = "Cancelled"
        except Exception as e:
            task.status = "failed"
            task.error = f"{type(e).__name__}: {e}"
            logger.warning("Background task '%s' (%s) failed: %s", task.name, task_id, e)
        finally:
            task.finished_at = time.time()
            _persist_task(repo_path, task)
            _running_tasks.pop(task_id, None)

    asyncio.create_task(_wrapped())
    return task


def get_background_task(task_id: str) -> BackgroundTask | None:
    return _running_tasks.get(task_id)


def list_background_tasks() -> list[BackgroundTask]:
    return list(_running_tasks.values())


def _tasks_dir(repo_path: str | Path) -> Path:
    return zircon_path(repo_path, "tasks")


def _persist_task(repo_path: str | Path, task: BackgroundTask) -> None:
    try:
        d = _tasks_dir(repo_path)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{task.id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to persist background task %s: %s", task.id, e)


def load_background_tasks(repo_path: str | Path) -> list[BackgroundTask]:
    d = _tasks_dir(repo_path)
    if not d.exists():
        return []
    tasks = []
    for p in sorted(d.glob("bg_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            tasks.append(BackgroundTask(**data))
        except Exception:
            continue
    return tasks