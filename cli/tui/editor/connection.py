"""
Editor connection discovery — find running editors via lock files.

Scans ~/.claude/ide/*.lock files for WebSocket connections from
VS Code, Zed, or other editors. The best-matching connection
(closest workspace folder, most recently modified lock file) wins.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EditorConnection:
    """A discovered editor connection."""

    url: str = ""
    auth_token: str = ""
    source: str = ""
    score: int = 0
    mtime: float = 0.0


def containment_score(parent: str, directory: str) -> int:
    """Score how well `parent` contains `directory`.

    Returns the path length of parent if directory is inside it, 0 otherwise.
    Longer parent paths = more specific = higher score.
    """
    try:
        resolved_parent = str(Path(parent).resolve())
        resolved_dir = str(Path(directory).resolve())
        rel = os.path.relpath(resolved_dir, resolved_parent)
        # If relative path doesn't start with .. and isn't absolute, directory is inside parent
        if rel == "." or (not rel.startswith("..") and not os.path.isabs(rel)):
            return len(resolved_parent)
        return 0
    except (ValueError, OSError):
        return 0


def discover_editor_connection(directory: str) -> EditorConnection | None:
    """
    Discover a running editor connection for the given directory.

    Scans ~/.claude/ide/*.lock files for WebSocket connections.
    Returns the best-matching connection or None.
    """
    home = Path.home()
    lock_dir = home / ".claude" / "ide"

    if not lock_dir.exists():
        return None

    connections: list[EditorConnection] = []

    try:
        for entry in lock_dir.iterdir():
            if not entry.name.endswith(".lock"):
                continue

            port_str = entry.stem  # filename without .lock
            try:
                port = int(port_str)
            except ValueError:
                continue
            if not (1 <= port <= 65535):
                continue

            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Only WebSocket transport
            transport = data.get("transport", "ws")
            if transport != "ws":
                continue

            # Check workspace folders contain our directory
            folders = data.get("workspaceFolders", [])
            folders = [f for f in folders if isinstance(f, str)]

            scores = [containment_score(f, directory) for f in folders]
            best_score = max(scores) if scores else 0
            if best_score == 0:
                continue

            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0

            connections.append(EditorConnection(
                url=f"ws://127.0.0.1:{port}",
                auth_token=data.get("authToken", ""),
                source=f"lock:{port}",
                score=best_score,
                mtime=mtime,
            ))
    except OSError:
        return None

    if not connections:
        return None

    # Best match: highest score (closest workspace), then most recent
    connections.sort(key=lambda c: (-c.score, -c.mtime))
    return connections[0]
