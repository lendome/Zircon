"""
Slot registry — manages plugin registration and slot rendering.

Plugins register their render functions for named slots. The registry
tracks all registrations and provides them to Slot instances.

Plugin errors are caught via the on_plugin_error callback — never crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .slots import SlotRegistration, SlotProps
from ..theming.theme import Theme

logger = logging.getLogger("agent.cli.tui.plugins")


@dataclass
class PluginEntry:
    """A registered plugin."""

    id: str
    name: str = ""
    version: str = ""
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


class SlotRegistry:
    """
    Manages plugin slot registrations.

    Plugins call register_slot() to add their render functions.
    The Slot component queries get_registrations() to find active plugins.

    Plugin render errors are caught and logged via on_plugin_error.
    """

    def __init__(
        self,
        theme: Theme | None = None,
        on_plugin_error: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.theme = theme
        self._registrations: dict[str, list[SlotRegistration]] = {}
        self._plugins: dict[str, PluginEntry] = {}
        self._on_error = on_plugin_error or self._default_error_handler

    def register_plugin(self, plugin: PluginEntry) -> Callable[[], None]:
        """Register a plugin. Returns an unregister function."""
        self._plugins[plugin.id] = plugin
        logger.info("Plugin registered: %s v%s", plugin.name, plugin.version)

        def _unregister() -> None:
            self._plugins.pop(plugin.id, None)
            # Remove all slot registrations for this plugin
            for slot_name in list(self._registrations.keys()):
                self._registrations[slot_name] = [
                    r for r in self._registrations[slot_name] if r.plugin_id != plugin.id
                ]
                if not self._registrations[slot_name]:
                    del self._registrations[slot_name]

        return _unregister

    def register_slot(
        self,
        plugin_id: str,
        slot_name: str,
        render_fn: Callable[[SlotProps], Any],
        priority: int = 0,
    ) -> Callable[[], None]:
        """Register a render function for a slot. Returns an unregister function."""
        reg = SlotRegistration(
            plugin_id=plugin_id,
            slot_name=slot_name,
            render_fn=render_fn,
            priority=priority,
        )
        self._registrations.setdefault(slot_name, []).append(reg)

        def _unregister() -> None:
            if slot_name in self._registrations:
                self._registrations[slot_name] = [
                    r for r in self._registrations[slot_name]
                    if r.plugin_id != plugin_id or r.priority != priority
                ]
                if not self._registrations[slot_name]:
                    del self._registrations[slot_name]

        return _unregister

    def get_registrations(self, slot_name: str) -> list[SlotRegistration]:
        """Get all registrations for a slot (from enabled plugins only)."""
        regs = self._registrations.get(slot_name, [])
        return [
            r for r in regs
            if self._plugins.get(r.plugin_id, PluginEntry(id=r.plugin_id, enabled=True)).enabled
        ]

    def get_plugins(self) -> list[PluginEntry]:
        return list(self._plugins.values())

    def is_plugin_enabled(self, plugin_id: str) -> bool:
        plugin = self._plugins.get(plugin_id)
        return plugin.enabled if plugin else False

    def dispose(self) -> None:
        """Clean up all registrations."""
        self._registrations.clear()
        self._plugins.clear()

    @staticmethod
    def _default_error_handler(event: dict[str, Any]) -> None:
        logger.error(
            "[tui.slot] plugin error: plugin=%s slot=%s phase=%s message=%s",
            event.get("plugin_id", "?"),
            event.get("slot", "?"),
            event.get("phase", "?"),
            event.get("message", "?"),
        )
