"""
Graceful fetch fallback — handle API version mismatches.

Wraps HTTP/transport calls with fallback to legacy defaults for
known endpoints when a 404 is returned.
"""

from __future__ import annotations

from typing import Any, Callable


_LEGACY_DEFAULTS: dict[str, Any] = {}


def register_legacy_default(path: str, default: Any) -> None:
    """Register a fallback value for a legacy endpoint path."""
    _LEGACY_DEFAULTS[path] = default


async def graceful_fetch(
    fetch_fn: Callable[..., Any],
    url: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Wrap a fetch call with graceful fallback.

    If the response is a 404, falls back to a registered legacy default.
    """
    try:
        response = await fetch_fn(url, *args, **kwargs)
    except Exception:
        response = None

    # Check for 404 status
    status = getattr(response, "status", None) or getattr(response, "status_code", None)
    if status != 404:
        return response

    # Fall back to legacy defaults
    path = _url_path(url)
    fallback = _LEGACY_DEFAULTS.get(path)
    if fallback is None:
        return response

    return fallback


def _url_path(url: str) -> str:
    """Extract the path component from a URL."""
    # Simple extraction — handles both http://host/path and /path
    if "://" in url:
        _, _, rest = url.partition("://")
        _, _, path = rest.partition("/")
        return "/" + path
    return url
