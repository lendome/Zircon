"""
Terminal renderer — manages the render loop, frame timing, and input capture.

Wraps Rich's Console as the rendering backend. The render loop runs at a
configurable target FPS. Mouse and key events are captured and dispatched
to registered handlers.

The renderer is an acquired resource — wrap in a context manager or call
destroy() explicitly:

    with Renderer(config) as renderer:
        renderer.run(app_root)

Config options:
  - target_fps:         render rate (default 60)
  - exit_on_ctrl_c:     if True, Ctrl+C exits the process; if False, the
                        app handles it itself (default False)
  - use_kitty_keyboard: full key event fidelity (default True)
  - use_mouse:          capture mouse events (default True)
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import Console, RenderableType
from rich.live import Live

from .dimensions import use_terminal_dimensions, dispose_dimensions


@dataclass
class RendererConfig:
    target_fps: int = 60
    exit_on_ctrl_c: bool = False
    use_kitty_keyboard: bool = True
    use_mouse: bool = True


class Renderer:
    """
    Terminal renderer with a reactive render loop.

    The render loop calls the root render function at target_fps. Between
    frames, it processes input events (keys, mouse) from the terminal.

    Components register render functions that return Rich renderables.
    The renderer diffs the output and only redraws changed regions.
    """

    def __init__(self, config: RendererConfig | None = None) -> None:
        self.config = config or RendererConfig()
        self.console = Console()
        self._live: Live | None = None
        self._running = False
        self._root_render: Callable[[], RenderableType] | None = None
        self._key_handlers: list[Callable[[str], bool]] = []
        self._mouse_handlers: list[Callable[[dict[str, Any]], bool]] = []
        self._frame_interval = 1.0 / max(1, self.config.target_fps)
        self._dimensions = use_terminal_dimensions()

    def set_root(self, render_fn: Callable[[], RenderableType]) -> None:
        """Set the root render function. Called every frame."""
        self._root_render = render_fn

    def on_key(self, handler: Callable[[str], bool]) -> None:
        """Register a key handler. Return True to consume the event."""
        self._key_handlers.append(handler)

    def on_mouse(self, handler: Callable[[dict[str, Any]], bool]) -> None:
        """Register a mouse handler. Return True to consume the event."""
        self._mouse_handlers.append(handler)

    def remove_key_handler(self, handler: Callable[[str], bool]) -> None:
        if handler in self._key_handlers:
            self._key_handlers.remove(handler)

    def remove_mouse_handler(self, handler: Callable[[dict[str, Any]], bool]) -> None:
        if handler in self._mouse_handlers:
            self._mouse_handlers.remove(handler)

    @property
    def dimensions(self) -> Any:
        return self._dimensions

    def render_once(self, renderable: RenderableType) -> None:
        """Render a single frame synchronously (non-loop mode)."""
        self.console.print(renderable)

    def start_live(self, renderable: RenderableType | None = None) -> None:
        """Start a Rich Live display for incremental updates."""
        initial = renderable or (self._root_render() if self._root_render else "")
        self._live = Live(
            initial,
            console=self.console,
            refresh_per_second=self.config.target_fps,
            transient=False,
        )
        self._live.start()

    def update_live(self, renderable: RenderableType) -> None:
        """Update the Live display with a new renderable."""
        if self._live is not None:
            self._live.update(renderable)
        else:
            self.console.print(renderable)

    def stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def run(self, render_fn: Callable[[], RenderableType] | None = None) -> None:
        """Run the synchronous render loop until stopped."""
        if render_fn is not None:
            self.set_root(render_fn)
        self._running = True
        try:
            while self._running:
                t0 = time.monotonic()
                if self._root_render is not None:
                    self.render_once(self._root_render())
                elapsed = time.monotonic() - t0
                remaining = self._frame_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        except KeyboardInterrupt:
            if self.config.exit_on_ctrl_c:
                return
            raise

    def stop(self) -> None:
        self._running = False

    def destroy(self) -> None:
        """Release all resources."""
        self.stop_live()
        self.stop()
        dispose_dimensions()

    def __enter__(self) -> "Renderer":
        return self

    def __exit__(self, *args: Any) -> None:
        self.destroy()
