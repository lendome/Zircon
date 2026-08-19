"""
Reactive signals — the atomic unit of reactivity.

A Signal wraps a value. Reading it inside a computed/effect registers a
dependency. Writing it notifies all dependents, triggering re-evaluation
of only the affected computed values and effects.

    count = signal(0)
    double = computed(() => count() * 2)
    count.set(5)  # double re-evaluates to 10
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


# ── Dependency tracking ──────────────────────────────────────────────────────

_active_observer: "Observer | None" = None


class Observer:
    """Base class for things that track signal dependencies (computed, effect)."""

    def __init__(self) -> None:
        self._dependencies: set["Signal[Any]"] = set()
        self._dirty = True
        self._disposed = False

    def track(self, sig: "Signal[Any]") -> None:
        self._dependencies.add(sig)

    def mark_dirty(self) -> None:
        self._dirty = True

    def dispose(self) -> None:
        for dep in list(self._dependencies):
            dep._unsubscribe(self)
        self._dependencies.clear()
        self._disposed = True


def _begin_tracking(observer: Observer) -> None:
    global _active_observer
    _active_observer = observer


def _end_tracking() -> None:
    global _active_observer
    _active_observer = None


def _current_observer() -> Observer | None:
    return _active_observer


# ── Signal ───────────────────────────────────────────────────────────────────


class Signal(Generic[T]):
    """A reactive value. Reading inside a computed/effect creates a dependency."""

    def __init__(self, initial: T) -> None:
        self._value: T = initial
        self._observers: set[Observer] = set()

    def __call__(self) -> T:
        observer = _current_observer()
        if observer is not None and not observer._disposed:
            observer.track(self)
            self._observers.add(observer)
        return self._value

    def get(self) -> T:
        return self.__call__()

    def set(self, value: T) -> None:
        if value == self._value:
            return
        self._value = value
        self._notify()

    def update(self, fn: Callable[[T], T]) -> None:
        self.set(fn(self._value))

    def _notify(self) -> None:
        for observer in list(self._observers):
            observer.mark_dirty()
            if isinstance(observer, _Computed):
                observer._recompute()
            elif isinstance(observer, _Effect):
                observer._run()

    def _unsubscribe(self, observer: Observer) -> None:
        self._observers.discard(observer)


def signal(initial: T) -> Signal[T]:
    """Create a reactive signal."""
    return Signal(initial)


# ── Computed ─────────────────────────────────────────────────────────────────


class _Computed(Observer, Generic[T]):
    """A derived value that re-evaluates when dependencies change."""

    def __init__(self, fn: Callable[[], T]) -> None:
        super().__init__()
        self._fn = fn
        self._value: T | None = None
        self._recompute()

    def __call__(self) -> T:
        if self._dirty:
            self._recompute()
        observer = _current_observer()
        if observer is not None and not observer._disposed:
            observer.track(self)  # type: ignore[arg-type]
        return self._value  # type: ignore[return-value]

    def get(self) -> T:
        return self.__call__()

    def _recompute(self) -> None:
        old_deps = set(self._dependencies)
        self._dependencies.clear()
        _begin_tracking(self)
        try:
            self._value = self._fn()
        finally:
            _end_tracking()
        for dep in old_deps - self._dependencies:
            dep._unsubscribe(self)
        self._dirty = False


def computed(fn: Callable[[], T]) -> _Computed[T]:
    """Create a computed value derived from signals."""
    return _Computed(fn)


# ── Effect ────────────────────────────────────────────────────────────────────


class _Effect(Observer):
    """A side effect that re-runs when dependencies change."""

    def __init__(self, fn: Callable[[], None]) -> None:
        super().__init__()
        self._fn = fn
        self._run()

    def _run(self) -> None:
        self._dependencies.clear()
        _begin_tracking(self)
        try:
            self._fn()
        finally:
            _end_tracking()
        self._dirty = False


def effect(fn: Callable[[], None]) -> _Effect:
    """Create a side effect that runs now and re-runs on dependency change."""
    return _Effect(fn)


# ── Persistent signal ─────────────────────────────────────────────────────────


def persistent_signal(name: str, initial: T, storage: Path | None = None) -> Signal[T]:
    """A signal that persists to disk (JSON) and restores on creation.

    Used for settings like sidebar state, theme mode, etc. that should
    survive across TUI restarts.
    """
    storage_path = storage or Path.cwd() / ".zircon-code" / "tui_state.json"

    if storage_path.exists():
        try:
            data = json.loads(storage_path.read_text(encoding="utf-8"))
            if name in data:
                initial = data[name]
        except (json.JSONDecodeError, OSError):
            pass

    sig = Signal(initial)

    def _persist(value: T) -> None:
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if storage_path.exists():
                existing = json.loads(storage_path.read_text(encoding="utf-8"))
            existing[name] = value
            storage_path.write_text(json.dumps(existing, default=str), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            pass

    original_set = sig.set

    def _set_and_persist(value: T) -> None:
        original_set(value)
        _persist(value)

    sig.set = _set_and_persist  # type: ignore[method-assign]
    return sig
