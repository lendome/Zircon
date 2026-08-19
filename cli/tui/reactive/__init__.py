"""
Reactive system — signals, computed values, stores, and lifecycle hooks.

The Python equivalent of SolidJS reactivity for the TUI. Components read
signals/computed values and automatically re-evaluate when dependencies
change. Only the affected components update — not a diff of the whole tree.
"""

from __future__ import annotations

from .signal import Signal, signal, computed, persistent_signal, effect
from .store import Store, create_store
from .lifecycle import on_mount, on_cleanup, LifecycleScope, current_scope

__all__ = [
    "Signal",
    "signal",
    "computed",
    "persistent_signal",
    "effect",
    "Store",
    "create_store",
    "on_mount",
    "on_cleanup",
    "LifecycleScope",
    "current_scope",
]
