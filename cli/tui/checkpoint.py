"""
Checkpoint management — Zircon-owned snapshot reversibility for agent turns.

Before every agent turn, Zircon saves a checkpoint under `.zircon-code` so
the user can revert if the agent ruins the codebase without modifying the
project's Git repository. The CheckpointPicker is a keyboard-driven TUI
(arrow keys) shown when the user double-presses Escape.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from .theming.theme import Theme


@dataclass
class Checkpoint:
    """A single Zircon-owned checkpoint snapshot."""
    sha: str
    message: str
    author: str = ""
    timestamp: float = 0.0
    files: list[str] = field(default_factory=list)


class CheckpointManager:
    """Manages git checkpoints via the transport layer.

    - create_checkpoint(label): called before every agent turn
    - list_checkpoints(): returns recent commits for the picker
    - revert_checkpoint(sha): reverts the working tree to a checkpoint
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    async def create_checkpoint(self, label: str = "") -> Checkpoint | None:
        """Create a checkpoint snapshot. Returns the checkpoint or None."""
        try:
            result = await self._transport.create_checkpoint(label)
            if result.get("ok") and result.get("checkpoint"):
                cp = result["checkpoint"]
                return Checkpoint(
                    sha=cp.get("sha", ""),
                    message=cp.get("message", ""),
                    author=cp.get("author", ""),
                    timestamp=cp.get("timestamp", 0.0),
                    files=cp.get("files", []),
                )
        except Exception:
            pass
        return None

    async def list_checkpoints(self, n: int = 20) -> list[Checkpoint]:
        """Return recent checkpoints."""
        try:
            result = await self._transport.list_checkpoints(n)
            if result.get("ok"):
                items = result.get("checkpoints", [])
                return [
                    Checkpoint(
                        sha=item.get("sha", ""),
                        message=item.get("message", ""),
                        author=item.get("author", ""),
                        timestamp=item.get("timestamp", 0.0),
                        files=item.get("files", []),
                    )
                    for item in items
                ]
        except Exception:
            pass
        return []

    async def revert_checkpoint(self, sha: str) -> bool:
        """Revert to a specific checkpoint. Returns True on success."""
        try:
            result = await self._transport.revert_checkpoint(sha)
            return bool(result.get("ok"))
        except Exception:
            return False


class CheckpointPicker:
    """Keyboard-driven checkpoint selector (arrow keys + Enter).

    Rendered as a Rich Panel. The caller routes keys to handle_key() while
    the picker is visible (picker.is_visible).
    """

    def __init__(self, checkpoints: list[Checkpoint], theme: Theme) -> None:
        self.checkpoints = checkpoints
        self.theme = theme
        self.index = 0
        self._visible = True
        self._result: Checkpoint | None = None
        self._cancelled = False

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def selected(self) -> Checkpoint | None:
        if self._result is not None:
            return self._result
        if self.checkpoints:
            return self.checkpoints[self.index]
        return None

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def move(self, direction: int) -> None:
        if self.checkpoints:
            self.index = (self.index + direction) % len(self.checkpoints)

    def confirm(self) -> None:
        if self.checkpoints:
            self._result = self.checkpoints[self.index]
        self._visible = False

    def cancel(self) -> None:
        self._cancelled = True
        self._visible = False

    def handle_key(self, key: str) -> None:
        """Route a key press. Returns True if the picker consumed it."""
        if key in ("escape", "ctrl+c", "q"):
            self.cancel()
            return
        if key in ("up", "ctrl+p", "k"):
            self.move(-1)
            return
        if key in ("down", "ctrl+n", "j"):
            self.move(1)
            return
        if key in ("return", "enter"):
            self.confirm()
            return

    def render(self) -> Panel:
        title = "Revert to Checkpoint"
        lines: list[Text] = []

        if not self.checkpoints:
            lines.append(Text("  No checkpoints available.", style=f"dim {self.theme.text_muted.to_rich()}"))
        else:
            lines.append(Text(
                "  ↑↓ navigate | Enter revert | Esc cancel",
                style=f"dim {self.theme.text_muted.to_rich()}",
            ))
            lines.append(Text(""))

            max_show = min(len(self.checkpoints), 12)
            # Window around the selected index for scrolling
            start = max(0, self.index - 5)
            end = min(len(self.checkpoints), start + max_show)
            if end - start < max_show:
                start = max(0, end - max_show)

            for i in range(start, end):
                cp = self.checkpoints[i]
                selected = i == self.index
                marker = "> " if selected else "  "
                style = f"bold {self.theme.primary.to_rich()}" if selected else ""

                # Format timestamp
                ts = ""
                if cp.timestamp:
                    try:
                        ts = time.strftime("%H:%M:%S", time.localtime(cp.timestamp))
                    except Exception:
                        ts = ""

                msg = cp.message[:60]
                if len(cp.message) > 60:
                    msg += "…"

                line = Text()
                line.append(marker, style=style)
                line.append(f"{cp.sha}  ", style=style)
                if ts:
                    line.append(f"{ts}  ", style=f"dim {self.theme.text_muted.to_rich()}" if not selected else style)
                line.append(msg, style=style)
                lines.append(line)

        return Panel(
            Group(*lines),
            title=title,
            border_style=self.theme.border_active.to_rich(),
        )
