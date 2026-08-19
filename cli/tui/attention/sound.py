"""
Sound packs — organized collections of sound files.

Each pack maps sound types to audio files. The default pack ships with
the app. Plugins can register custom packs. Sounds resolve through a
fallback chain: user override → active pack → builtin pack.

Sound types: default, question, permission, error, done, subagent_done
"""

from __future__ import annotations

import os
import sys
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class SoundType(str, Enum):
    DEFAULT = "default"
    QUESTION = "question"
    PERMISSION = "permission"
    ERROR = "error"
    DONE = "done"
    SUBAGENT_DONE = "subagent_done"


@dataclass
class SoundPack:
    """A collection of sound files."""

    id: str
    name: str = ""
    builtin: bool = False
    sounds: dict[str, str] = field(default_factory=dict)


BUILTIN_PACK = SoundPack(
    id="default",
    name="Default",
    builtin=True,
    sounds={
        SoundType.DEFAULT: "sounds/bip-bop-01.mp3",
        SoundType.QUESTION: "sounds/bip-bop-03.mp3",
        SoundType.PERMISSION: "sounds/staplebops-06.mp3",
        SoundType.ERROR: "sounds/nope-03.mp3",
        SoundType.DONE: "sounds/bip-bop-01.mp3",
        SoundType.SUBAGENT_DONE: "sounds/yup-01.mp3",
    },
)


def clamp_volume(volume: float) -> float:
    """Clamp volume to 0.0-1.0 range."""
    if volume is None or not isinstance(volume, (int, float)):
        return 0.0
    return min(1.0, max(0.0, float(volume)))


class Soundboard:
    """
    Manages sound packs and sound playback.

    - register_pack(): plugins register custom packs
    - activate(): switch the active pack
    - play(): resolve and play a sound through the fallback chain
    """

    def __init__(self) -> None:
        self._packs: dict[str, SoundPack] = {"default": BUILTIN_PACK}
        self._active_pack_id: str = "default"
        self._overrides: dict[str, str] = {}  # per-sound user overrides
        self._kv: Any = None  # KV store for persistence

    def register_pack(self, pack: SoundPack) -> Callable[[], None]:
        """Register a custom sound pack. Returns an unregister function."""
        if not pack.id or not pack.sounds:
            return lambda: None
        self._packs[pack.id] = pack

        def _unregister() -> None:
            if pack.id != "default":
                self._packs.pop(pack.id, None)
                if self._active_pack_id == pack.id:
                    self._active_pack_id = "default"

        return _unregister

    def activate(self, pack_id: str, persist: bool = False) -> bool:
        """Switch the active sound pack."""
        if pack_id not in self._packs:
            return False
        self._active_pack_id = pack_id
        if persist and self._kv is not None:
            self._kv.set("sound_pack", pack_id)
        return True

    @property
    def current(self) -> str:
        return self._active_pack_id

    def list_packs(self) -> list[dict[str, Any]]:
        """List all registered packs."""
        return [
            {
                "id": p.id,
                "name": p.name,
                "active": p.id == self._active_pack_id,
                "builtin": p.builtin,
            }
            for p in self._packs.values()
        ]

    def sound_candidates(self, sound_name: str) -> list[str]:
        """Get sound file candidates through the fallback chain."""
        candidates: list[str] = []
        # 1. User override for this specific sound
        if sound_name in self._overrides:
            candidates.append(self._overrides[sound_name])
        # 2. Active sound pack
        active_pack = self._packs.get(self._active_pack_id)
        if active_pack and sound_name in active_pack.sounds:
            candidates.append(active_pack.sounds[sound_name])
        # 3. Builtin fallback
        if sound_name in BUILTIN_PACK.sounds:
            candidates.append(BUILTIN_PACK.sounds[sound_name])
        # Deduplicate preserving order
        seen = set()
        return [c for c in candidates if not (c in seen or seen.add(c))]

    def play(self, sound_name: str, volume: float = 0.4) -> bool:
        """Play a sound. Resolves through the fallback chain."""
        candidates = self.sound_candidates(sound_name)
        vol = clamp_volume(volume)

        for candidate in candidates:
            if _try_play_sound(candidate, vol):
                return True
        return False

    def set_override(self, sound_name: str, file_path: str) -> None:
        """Set a user override for a specific sound."""
        self._overrides[sound_name] = file_path

    def set_kv_store(self, kv: Any) -> None:
        """Set the KV store for persistence."""
        self._kv = kv


def _try_play_sound(file_path: str, volume: float) -> bool:
    """Try to play a sound file. Returns True on success."""
    if not file_path:
        return False

    # If the file doesn't exist, try as a relative path from the package
    if not os.path.isabs(file_path):
        here = os.path.dirname(__file__)
        for parent in [here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))]:
            candidate = os.path.join(parent, file_path)
            if os.path.exists(candidate):
                file_path = candidate
                break

    if not os.path.exists(file_path):
        return False

    try:
        if sys.platform == "win32":
            import winsound
            # Windows: try winsound for WAV files
            if file_path.endswith(".wav"):
                flags = winsound.SND_FILENAME
                if volume < 0.5:
                    flags |= winsound.SND_ASYNC
                winsound.PlaySound(file_path, flags)
                return True
            # For MP3: use the default system sound as fallback
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return True
        else:
            # Unix: try aplay or afplay
            import subprocess
            if sys.platform == "darwin":
                subprocess.run(["afplay", file_path], timeout=5, capture_output=True)
                return True
            else:
                subprocess.run(["aplay", file_path], timeout=5, capture_output=True)
                return True
    except Exception:
        return False
