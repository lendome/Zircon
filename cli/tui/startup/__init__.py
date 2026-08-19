"""
Startup, error handling & lifecycle.

Structured resource management with scoped cleanup, loading screen,
theme prewarming, update checking, epilogue text, and onboarding.
"""

from __future__ import annotations

from .scope import ScopedLifecycle, acquire, acquire_resource, add_finalizer
from .loading import StartupLoading, StartupConfig
from .prewarm import prewarm_theme
from .update import UpdateChecker, is_version_greater, parse_version
from .epilogue import EpilogueManager
from .errors import extract_error_message, handle_session_error

__all__ = [
    "ScopedLifecycle",
    "acquire",
    "acquire_resource",
    "add_finalizer",
    "StartupLoading",
    "StartupConfig",
    "prewarm_theme",
    "UpdateChecker",
    "is_version_greater",
    "parse_version",
    "EpilogueManager",
    "extract_error_message",
    "handle_session_error",
]
