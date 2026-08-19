from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..core.exclusions import is_excluded, rg_exclude_args
from .base import Tool

_RG_TIMEOUT = 15.0
_MAX_SCAN_FILES = 2000
_SCAN_TIMEOUT = 20.0


async def _run_rg_in_thread(rg_args: list[str]) -> str | None:
    """Run ripgrep off the event loop with a hard timeout.

    The raw synchronous subprocess.re().run() would block the asyncio event
    loop for the full wait, freezing every session server-wide. Offlining it to
    a worker thread and wrapping it in asyncio.wait_for keeps the server
    responsive even when a search is slow or hangs.
    """
    from ..core.proc_spawn import popen_kwargs

    kwargs = popen_kwargs()

    def _blocking() -> subprocess.CompletedProcess:
        return subprocess.run(
            rg_args, capture_output=True, text=True, timeout=_RG_TIMEOUT, **kwargs
        )

    try:
        proc = await asyncio.wait_for(
            asyncio.to_thread(_blocking), timeout=_RG_TIMEOUT + 5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, asyncio.TimeoutError):
        return None
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout
    return None


class GrepCodeTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "grep_code"

    @property
    def description(self) -> str:
        return (
            "Search file contents for a regex pattern. "
            "Returns matching lines with file paths and line numbers."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (optional)"},
                "include": {"type": "string", "description": "File glob filter (e.g. '*.py')"},
            },
            "required": ["pattern"],
        }

    async def run(self, pattern: str, path: str | None = None, include: str | None = None) -> str:
        base = self._resolve(path) if path else self.repo_path
        if not base.exists():
            return f"Error: path not found: {path or '.'}"
        if is_excluded(base):
            return f"Error: path not found: {path or '.'}"

        rg_args = ["rg", "--no-heading", "--line-number", "--color", "never", "--max-count", "50"]
        if include:
            rg_args.extend(["--glob", include])
        rg_args.extend(rg_exclude_args())
        rg_args.extend([pattern, str(base)])
        out = await _run_rg_in_thread(rg_args)
        if out:
            return out[:5000]

        results = []
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Invalid regex: {e}"

        results = await asyncio.wait_for(
            asyncio.to_thread(self._fallback_scan, regex, base, include),
            timeout=_SCAN_TIMEOUT,
        )
        if not results:
            return f"No matches for '{pattern}'"
        return "\n".join(results)

    def _fallback_scan(self, regex, base: Path, include: str | None) -> list[str]:
        """Pure-Python fallback grep, run off the event loop (see _run_rg_in_thread).

        The recursive rglob walk plus the per-line pipeline would block the
        asyncio loop and freeze every session, so it is executed on a worker
        thread and wrapped in asyncio.wait_for by the caller.
        """
        results: list[str] = []
        files = [base] if base.is_file() else base.rglob(include or "*")
        scanned = 0
        for fp in files:
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                break
            if not fp.is_file() or is_excluded(fp):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = fp.relative_to(self.repo_path) if fp.is_relative_to(self.repo_path) else fp
                    results.append(f"{rel}:{i}: {line.strip()}")
                    if len(results) >= 50:
                        break
            if len(results) >= 50:
                break
        return results

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class FindSymbolsTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "find_symbols"

    @property
    def description(self) -> str:
        return (
            "Find symbol definitions (functions, classes, methods) by name. "
            "Returns file paths and line numbers."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Symbol name to search for"},
                "type": {"type": "string", "description": "Symbol type: 'function', 'class', 'any' (default)"},
            },
            "required": ["name"],
        }

    async def run(self, name: str, type: str = "any") -> str:
        results = await asyncio.wait_for(
            asyncio.to_thread(self._python_symbol_scan, name, type),
            timeout=_SCAN_TIMEOUT,
        )

        if not results:
            source_exts = "*.py *.js *.ts *.jsx *.tsx *.sqf *.cpp *.hpp *.c *.h *.cs *.java *.kt *.go *.rs *.rb *.php *.swift *.lua *.sh *.sql"
            rg_args = [
                "rg", "--no-heading", "--line-number", "--color", "never",
                "--max-count", "30", "--type-add", f"source:{source_exts}",
                "-tsource", "-i",
                rf"(?:function|def|fn|func|class|struct|enum|var|let|const|\w+_fnc_)\s+{re.escape(name)}",
                str(self.repo_path),
            ]
            out = await _run_rg_in_thread(rg_args)
            if out:
                results.extend(out.strip().splitlines()[:30])

        if not results:
            return f"No symbols matching '{name}'"
        return "\n".join(results)

    def _python_symbol_scan(self, name: str, type: str) -> list[str]:
        """Resolve symbol defs via AST parse of every *.py, off the event loop.

        rglob plus ASTParser.extract_symbols is blocking I/O and CPU work; running
        it directly on the loop would freeze all sessions. The caller wraps this
        in asyncio.to_thread + asyncio.wait_for.
        """
        from ..parsers.ast_parser import ASTParser
        parser = ASTParser()
        results: list[str] = []
        scanned = 0
        for py_file in self.repo_path.rglob("*.py"):
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                break
            if is_excluded(py_file):
                continue
            try:
                symbols = parser.extract_symbols(py_file)
            except Exception:
                continue
            for sym in symbols:
                if name.lower() not in sym["name"].lower():
                    continue
                if type != "any" and sym.get("kind") != type:
                    continue
                rel = py_file.relative_to(self.repo_path)
                results.append(f"{rel}:{sym['line']}: {sym['kind']} {sym['name']}")
                if len(results) >= 30:
                    break
            if len(results) >= 30:
                break
        return results


class GetStructureTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_structure"

    @property
    def description(self) -> str:
        return (
            "Get the symbol outline (functions, classes, methods, types) of a "
            "source file, or of EVERY source file in a directory at once. "
            "Supports Python, Go, JavaScript/TypeScript, Rust. Much cheaper "
            "than read_file for understanding what code does — prefer it for "
            "overviews and 'what is in this folder/file' questions."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory path"},
                "recursive": {"type": "boolean", "description": "Include nested source files (default: false)"},
                "max_files": {"type": "integer", "description": "Maximum files to outline, capped at 100 (default: 50)"},
            },
            "required": ["path"],
        }

    _MAX_DIR_FILES = 50

    async def run(self, path: str, recursive: bool = False, max_files: int = 50) -> str:
        target = self._resolve(path)

        def _compute() -> str:
            if target.is_dir():
                return self._outline_directory(target, path, recursive, max_files)
            if not target.is_file():
                return f"Error: file not found: {path}"
            body = self._outline_file(target)
            return body if body else f"No symbols found in {path}"

        return await asyncio.wait_for(
            asyncio.to_thread(_compute),
            timeout=_SCAN_TIMEOUT,
        )

    def _outline_file(self, target: Path) -> str:
        from ..parsers.ast_parser import ASTParser
        try:
            symbols = ASTParser().extract_symbols(target)
        except Exception as e:
            return f"Error parsing {target}: {e}"
        if not symbols:
            return ""
        lines = []
        for sym in symbols:
            indent = "  " if sym.get("parent") else ""
            lines.append(f"{sym['line']:>4}: {indent}{sym['kind']} {sym['name']}")
        return "\n".join(lines)

    def _outline_directory(self, target: Path, as_given: str, recursive: bool = False, max_files: int = 50) -> str:
        """Outline every file in a dir (AST parse + read — CPU/IO).

        Called via asyncio.to_thread from run(); parsing up to _MAX_DIR_FILES
        files synchronously would block the loop, so run() offlines this worker.
        """
        from ..parsers.ast_parser import SUPPORTED_SUFFIXES

        candidates = target.rglob("*") if recursive else target.iterdir()
        files = sorted(
            p for p in candidates
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and not is_excluded(p)
        )
        if not files:
            return f"No source files found in {as_given}"

        limit = max(1, min(int(max_files), 100))
        shown = files[:limit]
        sections: list[str] = []
        for f in shown:
            try:
                line_count = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            except Exception:
                line_count = 0
            body = self._outline_file(f)
            display = f.relative_to(target).as_posix()
            header = f"── {display} ({line_count} lines)"
            sections.append(f"{header}\n{body}" if body else f"{header}\n  (no symbols)")

        if len(files) > len(shown):
            sections.append(
                f"…and {len(files) - len(shown)} more files "
                f"(call get_structure on them individually)"
            )
        return "\n\n".join(sections)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class FaultLocalizeTool(Tool):
    """Hierarchical fault-localization tool: narrows a bug report to a surgical
    ~50-line context snippet (file-level IR -> structural parse -> line window).

    The heavy lifting is done by core.fault_localizer.FaultLocalizer; this tool
    is a thin async wrapper so the agent can invoke localization on demand.
    A ``localizer_factory`` callable (returning a ready ``FaultLocalizer``) is
    injected by the Agent so the tool can reuse the shared router/embedder.
    """

    def __init__(
        self,
        repo_path: str,
        localizer_factory: Callable[[], Any] | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self._localizer_factory = localizer_factory

    @property
    def name(self) -> str:
        return "fault_localize"

    @property
    def description(self) -> str:
        return (
            "Localize the root cause of a bug to a surgical code region. "
            "Given a bug report, runs file-level IR (BM25 + embeddings), "
            "structural parsing, and line-level pinpointing, returning the "
            "candidate files, suspect functions, and a ~50-line context snippet "
            "around the most likely buggy lines. Use this BEFORE editing when "
            "investigating a bug instead of many grep/read calls."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bug_report": {
                    "type": "string",
                    "description": "Description of the bug: symptom, error, expected vs actual behavior, stack trace.",
                },
            },
            "required": ["bug_report"],
        }

    async def run(self, bug_report: str) -> str:
        factory = self._localizer_factory
        if factory is None:
            return (
                "Fault localization is not configured for this session "
                "(no localizer factory wired)."
            )
        try:
            localizer = factory()
        except Exception as e:
            return f"Error building fault localizer: {e}"
        try:
            result = await localizer.localize(bug_report)
        except Exception as e:
            return f"Error during fault localization: {e}"
        block = localizer.format_context_block(result)
        return block or (
            "Fault localization produced no usable result. The bug report may "
            "not match any source files, or no LLM/embedder was available."
        )
