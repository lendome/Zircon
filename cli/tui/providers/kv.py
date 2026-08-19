"""
KV provider — persistent key-value store with reactive signals.

Survives restarts. Each kv.signal(key, default) returns a reactive
getter/setter that reads/writes persistent storage automatically.

  sidebar = kv.signal("sidebar", "auto")
  animations = kv.signal("animations_enabled", True)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..context import Context, ContextRegistry
from ..reactive.signal import Signal, signal, persistent_signal
from .base import Provider


class KVStore:
    """
    Persistent key-value store with reactive signals.

    Values are persisted to a JSON file in the .zircon-code/ directory.
    Each kv.signal(key, default) returns a Signal that auto-persists
    on write.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._path = storage_path or Path.cwd() / ".zircon-code" / "kv_store.json"
        self._data: dict[str, Any] = self._load()
        self._signals: dict[str, Signal[Any]] = {}

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, default=str), encoding="utf-8")
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()
        if key in self._signals:
            self._signals[key].set(value)

    def signal(self, key: str, default: Any) -> Signal[Any]:
        """Get or create a reactive signal for a key. Auto-persists on write."""
        if key in self._signals:
            return self._signals[key]

        initial = self._data.get(key, default)
        sig = signal(initial)
        self._signals[key] = sig

        # Wrap set to auto-persist
        original_set = sig.set

        def _set_and_persist(value: Any) -> None:
            original_set(value)
            self._data[key] = value
            self._save()

        sig.set = _set_and_persist  # type: ignore[method-assign]
        return sig

    def has(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._signals.pop(key, None)
        self._save()


class KVProvider(Provider):
    name = "kv"

    def __init__(self, workspace: str = ".") -> None:
        self._workspace = workspace

    def provide(self, registry: ContextRegistry) -> Any:
        storage = Path(self._workspace) / ".zircon-code" / "kv_store.json"
        kv = KVStore(storage)
        ctx = Context(name=self.name)
        ctx.set(kv)
        registry.register(ctx)
        return kv
