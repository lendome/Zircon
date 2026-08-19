"""
Plugin API adapter — typed access to TUI internals for plugins.

Plugins receive this on startup. It gives controlled access to:
  - dialog: dialog system (replace, clear, push)
  - keymap: keymap (dispatch, register, intercept)
  - kv: persistent key-value store
  - route: navigation
  - event: event bus
  - sdk: transport (HTTP client equivalent)
  - sync: reactive data store
  - theme: theme state
  - toast: toast notifications
  - renderer: terminal renderer
  - attention: notification/sound system
  - Slot: slot rendering function
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TuiPluginApi:
    """Typed API that plugins receive on startup."""

    version: str = "1.0.0"
    config: Any = None
    dialog: Any = None
    keymap: Any = None
    kv: Any = None
    route: Any = None
    routes: Any = None
    event: Any = None
    sdk: Any = None
    sync: Any = None
    theme: Any = None
    toast: Any = None
    renderer: Any = None
    attention: Any = None
    slot: Any = None
    registry: Any = None  # slot registry for registering slots


def create_tui_api(
    version: str = "1.0.0",
    config: Any = None,
    dialog: Any = None,
    keymap: Any = None,
    kv: Any = None,
    route: Any = None,
    routes: Any = None,
    event: Any = None,
    sdk: Any = None,
    sync: Any = None,
    theme: Any = None,
    toast: Any = None,
    renderer: Any = None,
    attention: Any = None,
    slot: Any = None,
    registry: Any = None,
) -> TuiPluginApi:
    """Create the typed plugin API from TUI internals."""
    return TuiPluginApi(
        version=version,
        config=config,
        dialog=dialog,
        keymap=keymap,
        kv=kv,
        route=route,
        routes=routes,
        event=event,
        sdk=sdk,
        sync=sync,
        theme=theme,
        toast=toast,
        renderer=renderer,
        attention=attention,
        slot=slot,
        registry=registry,
    )
