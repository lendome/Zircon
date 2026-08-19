"""
Plugin system & extensibility — slot-based plugin architecture.

Plugins can:
  - Replace UI elements via named slots (replace, single_winner, append)
  - Register custom routes
  - Add themes and sound packs at runtime
  - Add commands to the palette
  - Receive typed API access to TUI internals
  - Be individually enabled/disabled via config

Plugin errors are caught and logged — never crash the TUI.
The CLI provides a no-op host by default (no plugins loaded).
"""

from __future__ import annotations

from .slots import Slot, SlotMode, SlotProps
from .registry import SlotRegistry, PluginEntry
from .api import TuiPluginApi, create_tui_api
from .host import PluginHost, PluginSpec, PluginRuntime, NoOpPluginHost
from .routes import PluginRouteRegistry

__all__ = [
    "Slot",
    "SlotMode",
    "SlotProps",
    "SlotRegistry",
    "PluginEntry",
    "TuiPluginApi",
    "create_tui_api",
    "PluginHost",
    "PluginSpec",
    "PluginRuntime",
    "NoOpPluginHost",
    "PluginRouteRegistry",
]
