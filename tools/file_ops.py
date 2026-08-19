from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.exclusions import ZIRCON_DIR, is_excluded
from parsers.document_parser import classify, extract_text

from .base import Tool

_SCAN_TIMEOUT = 20.0


def _blocked(path: str) -> str:
    return f"Error: {path} is inside {ZIRCON_DIR}/ and is not readable."


async def _scan_in_thread(func: Any, *args: Any) -> Any:
    """Run a blocking filesystem scan (glob/iterdir walks) off the event loop.

    Recursive global-glob walks and directory listings are synchronous I/O;
    running them directly on the asyncio loop would freeze every open session.
    Offline to a worker thread and bound with a timeout to keep the server
    responsive even when a scan is slow or hangs on a network mount.
    """
    return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=_SCAN_TIMEOUT)


class ReadFileTool(Tool):
    DEFAULT_MAX_LINES = 400
    # Explicit start/end ranges may exceed the default window - the model
    # asked deliberately - but stay bounded to protect the context budget.
    HARD_MAX_LINES = 2000

    def __init__(self, repo_path: str, max_lines: int = DEFAULT_MAX_LINES):
        self.repo_path = Path(repo_path).resolve()
        self.max_lines = max(1, max_lines)

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            f"Read the contents of a file. Returns up to {self.max_lines} lines by default; "
            f"explicit start/end ranges may span up to {self.HARD_MAX_LINES} lines. "
            "Use start/end to paginate. Use scroll_up/scroll_down/goto_line for navigation."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute file path"},
                "start": {"type": "integer", "description": "Start line (1-indexed, optional)"},
                "end": {"type": "integer", "description": "End line (1-indexed, optional)"},
            },
            "required": ["path"],
        }

    async def run(self, path: str, start: int | None = None, end: int | None = None) -> str:
        target = self._resolve(path)
        if is_excluded(path) or is_excluded(target):
            return _blocked(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        try:
            raw = target.read_bytes()
            kind = classify(str(target), raw)
            if kind and kind != "text":
                # Documents (pdf/docx/epub/xps/cbz) are extracted off the loop
                # since MuPDF/OOXML parsing is blocking synchronous I/O.
                text, meta = await asyncio.to_thread(extract_text, kind, str(target), raw)
                _m = " | ".join(f"{k}={v}" for k, v in meta.items())
                return f"[{_m}]\n{text}"
            lines = raw.decode("utf-8", errors="replace").splitlines()
            total = len(lines)
            s = max(1, start or 1) - 1
            # Enforce line-window constraints
            if end is None:
                e = min(total, s + self.max_lines)
            else:
                e = min(total, end)
                # Explicit ranges get a much higher ceiling than default reads.
                if (e - s) > self.HARD_MAX_LINES:
                    e = min(total, s + self.HARD_MAX_LINES)
            selected = lines[s:e]
            numbered = [f"{i + s + 1}: {line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            result = f"[Lines {s + 1}-{e} of {total}]\n{result}"
            if e < total:
                result += f"\n... ({total - e} more lines available. Use end={e + 1} or scroll_down to continue.)"
            return result
        except Exception as e:
            return f"Error reading {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self.repo_path / p


class CreateFileTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "create_file"

    @property
    def description(self) -> str:
        return "Create a new file with the given content. Fails if the file already exists."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to create"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        }

    async def run(self, path: str, content: str) -> str:
        target = self._resolve(path)
        if target.exists():
            # Idempotency: if the on-disk content is already EXACTLY what the
            # caller wants, the desired end state is reached — report success
            # instead of an error. Without this, models that re-emit an
            # identical create_file call (e.g. after an ambiguous tool result)
            # get a failure and retry the same call in a loop.
            try:
                existing = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                existing = None
            # Compare newline-normalized: text-mode writes translate \n to the
            # platform separator, so a byte comparison would false-negative on
            # Windows (content with \r\n is not what lands on disk).
            if existing is not None and existing.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                return (
                    f"OK: {path} already exists with byte-identical content "
                    f"({len(content)} chars) — nothing to do. Do NOT call "
                    f"create_file for this path again; the file is already "
                    f"exactly as intended. Move on to the next step."
                )
            return (
                f"Error: file already exists: {path} (with DIFFERENT content). "
                f"Do NOT retry create_file for this path — it will keep failing. "
                f"Use read_file to inspect the current content, then edit_file "
                f"or edit_lines to make targeted changes."
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" disables text-mode translation: without it, Windows
            # turns every \n into \r\n, so content that already has \r\n
            # (e.g. a .bat file) lands on disk as \r\r\n — corrupted.
            target.write_text(content, encoding="utf-8", newline="")
            return f"Created {path} ({len(content)} chars)"
        except Exception as e:
            return f"Error creating {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class GlobFilesTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "glob_files"

    @property
    def description(self) -> str:
        return "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts')."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match"},
                "path": {"type": "string", "description": "Base directory to search in (optional)"},
            },
            "required": ["pattern"],
        }

    async def run(self, pattern: str, path: str | None = None) -> str:
        base = self._resolve(path) if path else self.repo_path
        if not base.is_dir():
            return f"Error: directory not found: {path or '.'}"
        if is_excluded(base):
            return _blocked(path or ".")
        def _scan() -> list[str]:
            return sorted(
                str(p.relative_to(self.repo_path))
                for p in base.glob(pattern)
                if p.is_file() and not is_excluded(p)
            )
        try:
            matches = await _scan_in_thread(_scan)
        except asyncio.TimeoutError:
            return f"Error: glob scan timed out: '{pattern}'"
        if not matches:
            return f"No files matching '{pattern}'"
        return "\n".join(matches)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class ListDirTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories in a directory."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: repo root)"},
            },
            "required": [],
        }

    async def run(self, path: str | None = None) -> str:
        base = self._resolve(path) if path else self.repo_path
        if not base.is_dir():
            return f"Error: not a directory: {path or '.'}"
        if is_excluded(base):
            return _blocked(path or ".")
        def _scan() -> list[str]:
            entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = []
            for entry in entries:
                if is_excluded(entry):
                    continue
                if entry.is_dir():
                    lines.append(f"  {entry.name}/")
                else:
                    lines.append(f"  {entry.name}")
            return lines
        try:
            lines = await _scan_in_thread(_scan)
        except asyncio.TimeoutError:
            return f"Error: listing timed out: {path or '.'}"
        return f"{base}:\n" + "\n".join(lines)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class DeleteFileTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "delete_file"

    @property
    def description(self) -> str:
        return "Delete a file."

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to delete"},
            },
            "required": ["path"],
        }

    async def run(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        try:
            target.unlink()
            return f"Deleted {path}"
        except Exception as e:
            return f"Error deleting {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class ScrollUpTool(Tool):
    """Scroll up (toward the beginning) in a file by a fixed window.

    This is a discrete navigation command for the constrained file viewer.
    It reads a window of lines ending at the current position, moving
    toward line 1.
    """

    DEFAULT_WINDOW = 100

    def __init__(self, repo_path: str, window: int = DEFAULT_WINDOW):
        self.repo_path = Path(repo_path).resolve()
        self.window = max(1, window)

    @property
    def name(self) -> str:
        return "scroll_up"

    @property
    def description(self) -> str:
        return (
            f"Scroll up (toward the beginning) in a file by {self.window} lines. "
            "Use this after read_file to navigate toward earlier lines. "
            "Provide the current end line to scroll up from."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to scroll in"},
                "end": {"type": "integer", "description": "Current end line (1-indexed). Scrolling reads the window ending here."},
                "window": {"type": "integer", "description": f"Number of lines to scroll (default {self.window})"},
            },
            "required": ["path", "end"],
        }

    async def run(self, path: str, end: int, window: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        w = window if window is not None else self.window
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            e = min(total, max(1, end))
            s = max(1, e - w)
            selected = lines[s - 1:e]
            numbered = [f"{i + s}: {line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            result = f"[Scroll up: Lines {s}-{e} of {total}]\n{result}"
            if s > 1:
                result += f"\n... ({s - 1} more lines above. Use scroll_up again or goto_line.)"
            return result
        except Exception as e:
            return f"Error scrolling up in {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class ScrollDownTool(Tool):
    """Scroll down (toward the end) in a file by a fixed window.

    This is a discrete navigation command for the constrained file viewer.
    It reads a window of lines starting at the current position, moving
    toward the end of the file.
    """

    DEFAULT_WINDOW = 100

    def __init__(self, repo_path: str, window: int = DEFAULT_WINDOW):
        self.repo_path = Path(repo_path).resolve()
        self.window = max(1, window)

    @property
    def name(self) -> str:
        return "scroll_down"

    @property
    def description(self) -> str:
        return (
            f"Scroll down (toward the end) in a file by {self.window} lines. "
            "Use this after read_file to navigate toward later lines. "
            "Provide the current start line to scroll down from."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to scroll in"},
                "start": {"type": "integer", "description": "Current start line (1-indexed). Scrolling reads the window starting here."},
                "window": {"type": "integer", "description": f"Number of lines to scroll (default {self.window})"},
            },
            "required": ["path", "start"],
        }

    async def run(self, path: str, start: int, window: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        w = window if window is not None else self.window
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            s = max(1, start)
            e = min(total, s + w - 1)
            selected = lines[s - 1:e]
            numbered = [f"{i + s}: {line}" for i, line in enumerate(selected)]
            result = "\n".join(numbered)
            result = f"[Scroll down: Lines {s}-{e} of {total}]\n{result}"
            if e < total:
                result += f"\n... ({total - e} more lines below. Use scroll_down again or goto_line.)"
            return result
        except Exception as e:
            return f"Error scrolling down in {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class GotoLineTool(Tool):
    """Jump directly to a specific line in a file.

    This is a discrete navigation command for the constrained file viewer.
    It reads a window of lines centered on (or starting at) the target line.
    """

    DEFAULT_WINDOW = 100

    def __init__(self, repo_path: str, window: int = DEFAULT_WINDOW):
        self.repo_path = Path(repo_path).resolve()
        self.window = max(1, window)

    @property
    def name(self) -> str:
        return "goto_line"

    @property
    def description(self) -> str:
        return (
            f"Jump directly to a specific line in a file, showing a window of "
            f"{self.window} lines around it. Use this for precise navigation."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to navigate in"},
                "line": {"type": "integer", "description": "Target line number (1-indexed)"},
                "window": {"type": "integer", "description": f"Number of lines to show around target (default {self.window})"},
            },
            "required": ["path", "line"],
        }

    async def run(self, path: str, line: int, window: int | None = None) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        w = window if window is not None else self.window
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            target_line = max(1, min(line, total))
            half = w // 2
            s = max(1, target_line - half)
            e = min(total, s + w - 1)
            # If we hit the bottom, slide up to use the full window
            if e - s + 1 < w:
                s = max(1, e - w + 1)
            selected = lines[s - 1:e]
            numbered = [f"{i + s}: {line_text}" for i, line_text in enumerate(selected)]
            result = "\n".join(numbered)
            result = f"[goto_line {target_line} of {total} (showing {s}-{e})]\n{result}"
            if s > 1:
                result += f"\n... ({s - 1} lines above)"
            if e < total:
                result += f"\n... ({total - e} lines below)"
            return result
        except Exception as e:
            return f"Error navigating to line {line} in {path}: {e}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p
