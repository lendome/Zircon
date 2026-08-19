"""
Lifecycle hooks — mount/cleanup for setup/teardown.

Components use on_mount() and on_cleanup() to register side effects and
resource management. Cleanup runs when the owning LifecycleScope is disposed
(e.g., when a component unmounts or the app exits).
"""

from __future__ import annotations

from typing import Callable


class LifecycleScope:
    """Tracks on_mount/on_cleanup callbacks for a component or the app."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._mount_callbacks: list[Callable[[], None]] = []
        self._cleanup_callbacks: list[Callable[[], None]] = []
        self._mounted = False

    def __enter__(self) -> "LifecycleScope":
        self.mount()
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def on_mount(self, fn: Callable[[], None]) -> None:
        if self._mounted:
            fn()
        else:
            self._mount_callbacks.append(fn)

    def on_cleanup(self, fn: Callable[[], None]) -> None:
        self._cleanup_callbacks.append(fn)

    def mount(self) -> None:
        if self._mounted:
            return
        self._mounted = True
        for fn in self._mount_callbacks:
            fn()
        self._mount_callbacks.clear()

    def cleanup(self) -> None:
        for fn in reversed(self._cleanup_callbacks):
            try:
                fn()
            except Exception:
                pass
        self._cleanup_callbacks.clear()
        self._mounted = False


_current_scope: LifecycleScope | None = None


def current_scope() -> LifecycleScope:
    if _current_scope is None:
        raise RuntimeError("No active lifecycle scope — call within a component")
    return _current_scope


def set_scope(scope: LifecycleScope | None) -> None:
    global _current_scope
    _current_scope = scope


def on_mount(fn: Callable[[], None]) -> None:
    """Register a mount callback in the current scope."""
    current_scope().on_mount(fn)


def on_cleanup(fn: Callable[[], None]) -> None:
    """Register a cleanup callback in the current scope."""
    current_scope().on_cleanup(fn)
