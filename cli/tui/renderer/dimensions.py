"""
Reactive terminal dimensions — a signal that updates on resize.

    dimensions = use_terminal_dimensions()
    wide = computed(() => dimensions().width > 120)
    content_width = computed(() => dimensions().width - (sidebar_visible() ? 42 : 0) - 4)

When the terminal is resized, the signal updates and all dependents reflow.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Callable

from ..reactive.signal import Signal, signal


@dataclass(frozen=True)
class TerminalDimensions:
    width: int
    height: int

    def __call__(self) -> "TerminalDimensions":
        return self


def _get_dimensions() -> TerminalDimensions:
    try:
        size = shutil.get_terminal_size((80, 24))
        return TerminalDimensions(width=size.columns, height=size.lines)
    except Exception:
        return TerminalDimensions(width=80, height=24)


_dimensions_signal: Signal[TerminalDimensions] | None = None
_resize_task: asyncio.Task[None] | None = None


def use_terminal_dimensions() -> Signal[TerminalDimensions]:
    """Get a reactive signal of terminal dimensions. Updates on resize."""
    global _dimensions_signal, _resize_task

    if _dimensions_signal is not None:
        return _dimensions_signal

    _dimensions_signal = signal(_get_dimensions())

    async def _poll_resize() -> None:
        last = _dimensions_signal.get()
        while True:
            await asyncio.sleep(0.5)
            current = _get_dimensions()
            if current != last:
                _dimensions_signal.set(current)
                last = current

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _resize_task = loop.create_task(_poll_resize())
    except RuntimeError:
        pass

    return _dimensions_signal


def dispose_dimensions() -> None:
    """Clean up the resize polling task."""
    global _dimensions_signal, _resize_task
    if _resize_task is not None:
        _resize_task.cancel()
        _resize_task = None
    _dimensions_signal = None
