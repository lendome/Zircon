"""
Attention manager — the central notify API.

Combines focus tracking, OS notifications, and sound packs into a
single notify() call that handles all the logic:

  result = await attention.notify({
      message: "Task complete",
      title: "CLI",
      notification: { when: "blurred" },
      sound: { when: "always", name: "done", volume: 0.4 },
  })

Skip reasons: attention_disabled, renderer_destroyed, empty_message,
              focused, blurred, focus_unknown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .focus import FocusTracker, FocusState
from .notifications import trigger_notification, normalize_text
from .sound import Soundboard, SoundType, clamp_volume


AttentionWhen = Literal["always", "focused", "blurred"]


@dataclass
class AttentionConfig:
    """Configuration for the attention system."""

    enabled: bool = False
    notifications: bool = True
    sound: bool = True
    volume: float = 0.4
    sound_pack: str = "default"
    sounds: dict[str, str] = field(default_factory=dict)


@dataclass
class NotifyRequest:
    """A request to notify the user."""

    message: str = ""
    title: str = "CLI"
    notification_when: AttentionWhen = "blurred"
    sound_when: AttentionWhen | None = "always"
    sound_name: str = "default"
    sound_volume: float | None = None


@dataclass
class NotifyResult:
    """Result of a notify call."""

    ok: bool = False
    notification: bool = False
    sound: bool = False
    skipped: str | None = None  # skip reason


def focus_skip(when: AttentionWhen, focus_state: FocusState) -> str | None:
    """Check if a notification should be skipped based on focus state."""
    if when == "always":
        return None
    if focus_state == FocusState.UNKNOWN:
        return "focus_unknown"
    if when == "blurred" and focus_state == FocusState.FOCUSED:
        return "focused"
    if when == "focused" and focus_state == FocusState.BLURRED:
        return "blurred"
    return None


class AttentionManager:
    """
    Central attention system.

    - notify(): fire OS notification + sound based on focus state
    - Sound resolution through fallback chain
    - Text normalization for OS notifications
    - Disposable (cleans up focus handlers)
    """

    def __init__(
        self,
        config: AttentionConfig | None = None,
        focus: FocusTracker | None = None,
        soundboard: Soundboard | None = None,
    ) -> None:
        self.config = config or AttentionConfig()
        self.focus = focus or FocusTracker()
        self.soundboard = soundboard or Soundboard()
        self._renderer: Any = None
        self._disposed = False

    def set_renderer(self, renderer: Any) -> None:
        self._renderer = renderer

    def set_kv_store(self, kv: Any) -> None:
        self.soundboard.set_kv_store(kv)

    async def notify(self, request: NotifyRequest) -> NotifyResult:
        """Fire a notification + sound based on focus state and config."""
        if not self.config.enabled or self._disposed:
            return NotifyResult(skipped="attention_disabled")

        if self._renderer is None or getattr(self._renderer, "_destroyed", False):
            return NotifyResult(skipped="renderer_destroyed")

        message = normalize_text(request.message, "", 240)
        if not message:
            return NotifyResult(skipped="empty_message")

        focus_state = self.focus.current
        result = NotifyResult(ok=True)

        # OS notification
        if self.config.notifications:
            skip = focus_skip(request.notification_when, focus_state)
            if skip is None:
                title = normalize_text(request.title, "CLI", 80)
                if trigger_notification(self._renderer, message, title):
                    result.notification = True
            else:
                result.skipped = skip

        # Sound
        if self.config.sound:
            sound_skip = focus_skip(request.sound_when or "always", focus_state)
            if sound_skip is None:
                volume = request.sound_volume
                if volume is None:
                    volume = self.config.volume
                volume = clamp_volume(volume)
                if self.soundboard.play(request.sound_name, volume):
                    result.sound = True

        return result

    def notify_done(self, message: str = "Task complete") -> Any:
        """Convenience: notify that a task is done."""
        import asyncio
        return asyncio.ensure_future(self.notify(NotifyRequest(
            message=message,
            sound_name=SoundType.DONE,
        )))

    def notify_error(self, message: str = "An error occurred") -> Any:
        """Convenience: notify about an error."""
        import asyncio
        return asyncio.ensure_future(self.notify(NotifyRequest(
            message=message,
            sound_name=SoundType.ERROR,
        )))

    def notify_question(self, message: str = "Question requires answer") -> Any:
        """Convenience: notify about a question."""
        import asyncio
        return asyncio.ensure_future(self.notify(NotifyRequest(
            message=message,
            sound_name=SoundType.QUESTION,
        )))

    def dispose(self) -> None:
        """Clean up focus handlers."""
        if self._disposed:
            return
        self._disposed = True
