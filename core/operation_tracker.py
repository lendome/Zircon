from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable

from .types import TraceEvent

logger = logging.getLogger("agent.core.operation_tracker")


class OperationTracker:
    """Tracks elapsed time for a long operation and periodically yields status updates.

    Use as a context manager around long-awaited LLM calls to give the user
    visibility into what's taking so long.

    Example:
        tracker = OperationTracker(phase="analyzing", detail="Classifying project...")
        async for status in tracker.monitor():
            yield status  # periodic heads-up messages every few seconds
        # ... do the actual work ...
        tracker.finish()
    """

    STATUS_INTERVAL: float = 5.0  # seconds between status yields

    def __init__(self, phase: str, detail: str, max_expected: float = 10.0):
        self.phase = phase
        self.detail = detail
        self.max_expected = max_expected
        self.start_time = time.monotonic()
        self._last_yield = time.monotonic()
        self._status_messages: list[str] = []
        self._finished = False

    def note(self, msg: str) -> None:
        """Record an intermediate progress note."""
        elapsed = time.monotonic() - self.start_time
        self._status_messages.append(f"+{elapsed:.0f}s {msg}")
        logger.debug("OperationTracker note: %s", msg)

    def finish(self) -> None:
        """Mark the operation as finished."""
        self._finished = True
        elapsed = time.monotonic() - self.start_time
        logger.debug("OperationTracker finished in %.1fs: %s", elapsed, self.detail)

    async def monitor(self, yield_callback: Callable[[TraceEvent], None]) -> None:
        """Periodically yield status updates if the operation is taking too long.

        Call this in an async loop before/around the awaited operation.
        """
        if self._finished:
            return

        now = time.monotonic()
        elapsed = now - self.start_time
        since_last = now - self._last_yield

        if since_last < self.STATUS_INTERVAL and elapsed < self.max_expected:
            return  # too soon, nothing to report

        self._last_yield = now

        if elapsed < 10.0:
            # Just a quick "working" message
            msg = f"⏳ {self.detail} (elapsed: ~{int(elapsed)}s)"
        elif elapsed < 30.0:
            # Taking a while
            context = ""
            if self._status_messages:
                context = " | " + self._status_messages[-1]
            msg = (
                f"⏳ Still working on: {self.detail}{context} "
                f"({int(elapsed)}s so far)\n"
                f"   ℹ️  This is normal for LLM-based analysis. "
                f"Expect ~{int(self.max_expected)}-{int(self.max_expected * 2)}s typically."
            )
        elif elapsed < 60.0:
            context = ""
            if self._status_messages:
                context = "\n   · " + "\n   · ".join(self._status_messages[-3:])
            msg = (
                f"⏳ Still working on: {self.detail} — {int(elapsed)}s elapsed{context}\n"
                f"   ℹ️  This is taking longer than usual. "
                f"The LLM provider may be experiencing high load or the task is complex."
            )
        else:
            # Over a minute
            context = ""
            if self._status_messages:
                context = "\n   · " + "\n   · ".join(self._status_messages[-5:])
            msg = (
                f"⏳ Still working on: {self.detail} — {int(elapsed // 60)}m{int(elapsed % 60)}s elapsed{context}\n"
                f"   ⚠️  Taking quite long. Possible causes:\n"
                f"   · LLM provider rate limiting or high latency\n"
                f"   · Complex multi-sample consensus (quality tier)\n"
                f"   · Large context window processing"
            )

        yield_callback(TraceEvent(phase="status", detail=msg, payload={"elapsed_seconds": elapsed}))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time


async def monitor_operation(
    phase: str,
    detail: str,
    coro,
    yield_fn: Callable[[TraceEvent], None],
    max_expected: float = 10.0,
    status_interval: float = 5.0,
):
    """Run a coroutine while periodically yielding status updates.

    Args:
        phase: The phase label for TraceEvent
        detail: Description of what's being done
        coro: The async operation to await
        yield_fn: Callback to emit TraceEvent updates
        max_expected: Typical expected duration in seconds
        status_interval: How often to emit status in seconds
    """
    tracker = OperationTracker(phase, detail, max_expected=max_expected)
    tracker.STATUS_INTERVAL = status_interval

    async def _monitor():
        while True:
            try:
                async with asyncio.timeout(status_interval):
                    await asyncio.Event().wait()  # sleep until timeout or cancelled
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            if tracker._finished:
                break
            await tracker.monitor(yield_fn)

    monitor_task = asyncio.create_task(_monitor())
    try:
        result = await coro
        return result
    finally:
        tracker.finish()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass