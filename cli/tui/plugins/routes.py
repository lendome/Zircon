"""
Plugin route registry — custom routes registered by plugins.

Plugins can register custom routes that appear in the navigation.
The route system checks for plugin routes when the route type is "plugin".
"""

from __future__ import annotations

from typing import Any, Callable

from rich.console import RenderableType
from rich.text import Text as RichText


class PluginRouteRegistry:
    """
    Registry for plugin-provided routes.

    Plugins register render functions keyed by route ID. When the
    active route type is "plugin", the router looks up the render fn.
    """

    def __init__(self) -> None:
        self._routes: dict[str, Callable[[dict[str, Any]], RenderableType]] = {}

    def register(
        self,
        route_id: str,
        render_fn: Callable[[dict[str, Any]], RenderableType],
    ) -> Callable[[], None]:
        """Register a plugin route. Returns an unregister function."""
        self._routes[route_id] = render_fn

        def _unregister() -> None:
            self._routes.pop(route_id, None)

        return _unregister

    def get(self, route_id: str) -> Callable[[dict[str, Any]], RenderableType] | None:
        return self._routes.get(route_id)

    def render(self, route_id: str, params: dict[str, Any] | None = None) -> RenderableType:
        """Render a plugin route by ID."""
        render_fn = self._routes.get(route_id)
        if render_fn is None:
            return RichText(f"[Plugin route not found: {route_id}]", style="red")
        try:
            return render_fn(params or {})
        except Exception as exc:
            return RichText(f"[Plugin route error: {exc}]", style="red")

    def list_routes(self) -> list[str]:
        return list(self._routes.keys())

    def dispose(self) -> None:
        self._routes.clear()
