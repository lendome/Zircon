"""
Plugin host — manages plugin lifecycle (start/dispose).

Plugins are specified in the TUI config as:
  plugin: ["plugin-name", ["plugin-name", { option: value }]]

A plugin spec is either a string (name) or a tuple (name + options).
Plugins can be individually enabled/disabled.

The CLI provides a no-op host by default (no plugins loaded).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .api import TuiPluginApi
from .registry import SlotRegistry, PluginEntry
from .routes import PluginRouteRegistry


@dataclass
class PluginSpec:
    """A plugin specification from config."""

    name: str
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @staticmethod
    def parse(spec: Any) -> "PluginSpec":
        """Parse a config spec into a PluginSpec."""
        if isinstance(spec, str):
            return PluginSpec(name=spec)
        if isinstance(spec, (list, tuple)) and len(spec) >= 1:
            name = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            return PluginSpec(name=name, options=options)
        return PluginSpec(name=str(spec))


@dataclass
class PluginRuntime:
    """Runtime state for plugins — slots, routes, commands."""

    slots: SlotRegistry
    routes: PluginRouteRegistry
    commands: dict[str, Any] = field(default_factory=dict)
    themes: dict[str, Any] = field(default_factory=dict)
    sound_packs: dict[str, Any] = field(default_factory=dict)
    status: list[dict[str, Any]] = field(default_factory=list)

    def dispose(self) -> None:
        self.slots.dispose()
        self.routes.dispose()


class PluginHost:
    """
    Manages plugin lifecycle.

    - start(): initialize all enabled plugins, pass them the TUI API
    - dispose(): clean up all plugin resources

    The CLI provides a no-op host by default (no plugins loaded).
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginEntry] = {}
        self._runtime: PluginRuntime | None = None
        self._api: TuiPluginApi | None = None
        self._dispose_fns: list[Callable[[], None]] = []

    @property
    def runtime(self) -> PluginRuntime | None:
        return self._runtime

    def start(
        self,
        api: TuiPluginApi,
        config: dict[str, Any] | None = None,
        theme: Any = None,
    ) -> PluginRuntime:
        """
        Start all enabled plugins.

        Args:
            api: Typed TUI API for plugins
            config: Resolved TUI config with plugin specs
            theme: Theme for slot registry

        Returns:
            PluginRuntime with slots, routes, commands
        """
        cfg = config or {}
        slot_registry = SlotRegistry(theme=theme)
        route_registry = PluginRouteRegistry()
        self._runtime = PluginRuntime(slots=slot_registry, routes=route_registry)
        self._api = api

        # Parse plugin specs from config
        plugin_specs = cfg.get("plugin", [])
        plugin_enabled = cfg.get("plugin_enabled", {})

        for raw_spec in plugin_specs:
            spec = PluginSpec.parse(raw_spec)
            if not plugin_enabled.get(spec.name, True):
                continue

            entry = PluginEntry(
                id=spec.name,
                name=spec.name,
                config=spec.options,
                enabled=True,
            )
            self._plugins[spec.name] = entry

            # In a real implementation, this would dynamically load the plugin
            # module and call its setup() function. For now, we just register it.
            unregister = slot_registry.register_plugin(entry)
            self._dispose_fns.append(unregister)

            self._runtime.status.append({
                "id": spec.name,
                "name": spec.name,
                "enabled": True,
                "installed": True,
            })

        return self._runtime

    def dispose(self) -> None:
        """Clean up all plugin resources."""
        for fn in self._dispose_fns:
            try:
                fn()
            except Exception:
                pass
        self._dispose_fns.clear()

        if self._runtime is not None:
            self._runtime.dispose()
            self._runtime = None

        self._plugins.clear()
        self._api = None


class NoOpPluginHost(PluginHost):
    """A plugin host that loads no plugins — the CLI default."""

    def start(
        self,
        api: TuiPluginApi,
        config: dict[str, Any] | None = None,
        theme: Any = None,
    ) -> PluginRuntime:
        """Start with no plugins — just empty registries."""
        slot_registry = SlotRegistry(theme=theme)
        route_registry = PluginRouteRegistry()
        self._runtime = PluginRuntime(slots=slot_registry, routes=route_registry)
        self._api = api
        return self._runtime
