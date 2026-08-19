"""
Attention, audio & notifications.

Focus-aware notification system that fires OS notifications and sound
effects when the TUI is in the background. Supports sound packs with
a fallback chain, per-event configuration, and text normalization.
"""

from __future__ import annotations

from .focus import FocusState, FocusTracker
from .notifications import trigger_notification, normalize_text
from .sound import SoundType, SoundPack, Soundboard, BUILTIN_PACK
from .manager import AttentionManager, AttentionConfig, NotifyResult, NotifyRequest

__all__ = [
    "FocusState",
    "FocusTracker",
    "trigger_notification",
    "normalize_text",
    "SoundType",
    "SoundPack",
    "Soundboard",
    "BUILTIN_PACK",
    "AttentionManager",
    "AttentionConfig",
    "NotifyResult",
    "NotifyRequest",
]
