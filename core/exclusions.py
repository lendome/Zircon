"""Central place deciding what tools are never allowed to look at.

The runtime directory (``.zircon-code``) holds checkpoints, session state,
embeddings and caches. None of it is useful context for the model and dumping
it into a tool result is both noisy and a privacy leak, so every search /
listing / read path funnels through the helpers here.
"""

from __future__ import annotations

from pathlib import Path

from .constants import ZIRCON_DIR

EXCLUDED_DIRS: frozenset[str] = frozenset({ZIRCON_DIR})


def is_excluded(path: str | Path) -> bool:
    """True when *path* is, or lives inside, an excluded directory."""
    parts = Path(path).parts
    return any(part in EXCLUDED_DIRS for part in parts)


def filter_paths(paths):
    """Yield only the paths that are not excluded."""
    return (p for p in paths if not is_excluded(p))


def rg_exclude_args() -> list[str]:
    """Ripgrep flags that hide the excluded directories."""
    args: list[str] = []
    for name in sorted(EXCLUDED_DIRS):
        args.extend(["--glob", f"!{name}", "--glob", f"!{name}/**"])
    return args
