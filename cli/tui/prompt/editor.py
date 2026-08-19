"""
External editor integration — opens $EDITOR (or $VISUAL) to edit the
prompt in a full editor.

The renderer is suspended during editing so the editor gets full
terminal control. After editing, non-text parts (file mentions) have
their positions recalculated.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def open_external_editor(
    value: str,
    renderer: Any | None = None,
    cwd: str | None = None,
) -> str | None:
    """
    Open $EDITOR/$VISUAL to edit the prompt text.

    Args:
        value: The current prompt text.
        renderer: The renderer instance (for suspend/resume). If None,
                  editing happens in a subprocess without suspend.
        cwd: Working directory for the editor.

    Returns:
        The edited text, or None if the editor was not available or failed.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        return None

    # Create a temp file with the current text
    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="zircon_prompt_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)

        # Suspend the renderer if available
        if renderer is not None:
            if hasattr(renderer, "stop_live"):
                renderer.stop_live()

        try:
            # Run the editor with inherited stdio (full terminal control)
            proc = subprocess.run(
                [editor, tmp_path],
                cwd=cwd or os.getcwd(),
                stdin=sys.stdin,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            if proc.returncode != 0:
                return None
        finally:
            # Resume the renderer
            if renderer is not None:
                if hasattr(renderer, "clear_buffer"):
                    pass  # renderer will handle its own buffer
                if hasattr(renderer, "request_render"):
                    renderer.request_render()  # type: ignore[attr-defined]

        # Read the edited text
        return Path(tmp_path).read_text(encoding="utf-8")
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def reconcile_parts_after_edit(
    edited_text: str,
    parts: list[dict],
) -> list[dict]:
    """
    After external editor edit, recalculate part positions.

    For each non-text part, find its virtual_text in the edited text.
    If found, update positions. If not found, the part was deleted.
    """
    result: list[dict] = []
    for part in parts:
        if part.get("type") == "text":
            result.append(part)
            continue
        virtual_text = part.get("virtual_text", "")
        if not virtual_text:
            continue
        new_start = edited_text.find(virtual_text)
        if new_start == -1:
            # Virtual text was deleted — skip this part
            continue
        part["source_start"] = new_start
        part["source_end"] = new_start + len(virtual_text)
        result.append(part)
    return result
