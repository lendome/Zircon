"""
Clipboard provider — read/write clipboard with fallbacks.

Supports text and binary (image/file) clipboard content.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from ..context import Context, ContextRegistry
from .base import Provider


@dataclass
class ClipboardContent:
    """Content read from the clipboard."""

    data: str = ""
    mime: str = "text/plain"


class Clipboard:
    """
    Clipboard read/write with platform fallbacks.

    - Windows: uses `clip` (write) and `powershell Get-Clipboard` (read)
    - macOS: uses `pbcopy` / `pbpaste`
    - Linux: uses `xclip` or `xsel`
    """

    def write(self, text: str) -> bool:
        """Write text to the clipboard. Returns True on success."""
        try:
            if sys.platform == "win32":
                from ....core.proc_spawn import popen_kwargs
                subprocess.run(["clip"], input=text.encode("utf-8"), check=True, timeout=5,
                               **popen_kwargs())
                return True
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
                return True
            else:
                for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                    try:
                        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=5)
                        return True
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        continue
        except Exception:
            pass
        return False

    def read(self) -> ClipboardContent:
        """Read text from the clipboard."""
        try:
            if sys.platform == "win32":
                from ....core.proc_spawn import popen_kwargs
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=5,
                    **popen_kwargs(),
                )
                return ClipboardContent(data=result.stdout)
            elif sys.platform == "darwin":
                result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
                return ClipboardContent(data=result.stdout)
            else:
                for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                        return ClipboardContent(data=result.stdout)
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        continue
        except Exception:
            pass
        return ClipboardContent(data="")


class ClipboardProvider(Provider):
    name = "clipboard"

    def provide(self, registry: ContextRegistry) -> Any:
        clipboard = Clipboard()
        ctx = Context(name=self.name)
        ctx.set(clipboard)
        registry.register(ctx)
        return clipboard
