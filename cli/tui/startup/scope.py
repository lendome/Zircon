"""
Scoped lifecycle - structured resource management with cleanup.

Every resource is acquired with a cleanup callback. When the lifecycle
ends (or errors), all cleanup functions run in reverse order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class AcquiredResource(Generic[T]):
    value: T
    cleanup: Callable[[], None] | None = None


class ScopedLifecycle:
    def __init__(self) -> None:
        self._resources: list[AcquiredResource[Any]] = []
        self._finalizers: list[Callable[[], None]] = []
        self._error: Exception | None = None

    def acquire(self, value: T, on_release: Callable[[T], None] | None = None) -> T:
        cleanup: Callable[[], None] | None = None
        if on_release is not None:
            cleanup = lambda v=value, fn=on_release: fn(v)
        self._resources.append(AcquiredResource(value=value, cleanup=cleanup))
        return value

    def add_finalizer(self, fn: Callable[[], None]) -> None:
        self._finalizers.append(fn)

    @property
    def error(self) -> Exception | None:
        return self._error

    def cleanup(self) -> None:
        for fn in self._finalizers:
            try:
                fn()
            except Exception:
                pass
        self._finalizers.clear()
        for res in reversed(self._resources):
            if res.cleanup is not None:
                try:
                    res.cleanup()
                except Exception:
                    pass
        self._resources.clear()

    def __enter__(self) -> "ScopedLifecycle":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_val is not None:
            self._error = exc_val
        self.cleanup()
        return False


def acquire(value: T, on_release: Callable[[T], None] | None = None) -> AcquiredResource[T]:
    cleanup: Callable[[], None] | None = None
    if on_release is not None:
        cleanup = lambda v=value, fn=on_release: fn(v)
    return AcquiredResource(value=value, cleanup=cleanup)


def acquire_resource(value: T, on_release: Callable[[T], None] | None = None) -> AcquiredResource[T]:
    return acquire(value, on_release)


def add_finalizer(scope: ScopedLifecycle, fn: Callable[[], None]) -> None:
    scope.add_finalizer(fn)
