"""
Update checking — version comparison and update prompts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Version:
    core: list[int]
    prerelease: str = ""

    @staticmethod
    def parse(s: str) -> "Version":
        s = s.lstrip("v").strip()
        prerelease = ""
        if "-" in s:
            s, _, prerelease = s.partition("-")
        parts = [int(p) if p.isdigit() else 0 for p in s.split(".")]
        return Version(core=parts, prerelease=prerelease)


def parse_version(s: str) -> Version:
    return Version.parse(s)


def is_version_greater(left: str, right: str) -> bool:
    a, b = Version.parse(left), Version.parse(right)
    for i in range(max(len(a.core), len(b.core))):
        diff = (a.core[i] if i < len(a.core) else 0) - (b.core[i] if i < len(b.core) else 0)
        if diff:
            return diff > 0
    if a.prerelease == b.prerelease:
        return False
    if not a.prerelease:
        return True
    if not b.prerelease:
        return False
    return a.prerelease > b.prerelease


class UpdateChecker:
    """Checks for updates and manages skip-version persistence."""

    def __init__(self, kv: Any = None) -> None:
        self._kv = kv

    def should_show(self, version: str) -> bool:
        skipped = self._kv.get("skipped_version") if self._kv else None
        if skipped and not is_version_greater(version, skipped):
            return False
        return True

    def skip_version(self, version: str) -> None:
        if self._kv:
            self._kv.set("skipped_version", version)
