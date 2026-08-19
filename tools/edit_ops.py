from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .base import Tool
from ..core.edit_engine import EditEngine
from ..core.diff_display import make_unified_diff


class EditFileTool(Tool):
    def __init__(self, repo_path: str, approval_gate: Any = None):
        self.repo_path = Path(repo_path).resolve()
        self._engine = EditEngine()
        self._approval_gate = approval_gate

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file using SEARCH/REPLACE. Provide the exact text to search for "
            "and the replacement text. Uses fuzzy matching, whitespace normalization, "
            "and AST-aware fallbacks if exact match fails. "
            "Supports Aider-style <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks. "
            "Pass multiple {'search', 'replace'} objects via the 'edits' list to apply "
            "several replacements in one call. On success a before/after diff preview "
            "is reported. If a search cannot be matched, near-miss context hints are "
            "given to help locate the intended text."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "search": {"type": "string", "description": "Exact text to search for"},
                "replace": {"type": "string", "description": "Replacement text"},
                "edits": {
                    "type": "array",
                    "description": (
                        "Multiple replacements applied in one call. Each entry has "
                        "'search' and 'replace'. Edits run sequentially on the file."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "search": {"type": "string"},
                            "replace": {"type": "string"},
                        },
                        "required": ["search", "replace"],
                    },
                },
            },
            "required": ["path", "search", "replace"],
        }

    async def run(
        self,
        path: str,
        search: str = "",
        replace: str = "",
        edits: list[dict[str, Any]] | None = None,
    ) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"

        replacements = edits if edits else [{"search": search, "replace": replace}]
        if not replacements or not any((r.get("search") or "").strip() for r in replacements):
            return "Error: search string is empty"

        reports: list[str] = []
        failed: tuple[int, str, str] | None = None
        for i, r in enumerate(replacements):
            s = r.get("search", "")
            rep = r.get("replace", "")
            if not s.strip():
                reports.append(
                    f"[{i + 1}/{len(replacements)}] skipped: empty search string"
                )
                continue
            result = self._engine.propose_search_replace(target, s, rep)
            if not result.success:
                failed = (i + 1, s, result.error)
                break
            preview = make_unified_diff(result.path, result.old_content, result.new_content)
            if result.method != "exact" and self._approval_gate is not None:
                reason = (
                    f"Low-confidence {result.method} edit match "
                    f"(confidence={result.confidence:.0%}). Review the proposed diff before applying."
                )
                approval_args = {
                    "path": path,
                    "method": result.method,
                    "confidence": result.confidence,
                    "diff": preview[:12000],
                }
                if not await self._approval_gate.request("edit_file", approval_args, reason):
                    return f"Error: edit denied before applying ({result.method}, confidence={result.confidence:.0%}).\n{preview}"
            result = self._engine.apply_proposal(target, result)
            if not result.success:
                failed = (i + 1, s, result.error)
                break
            prefix = f"[{i + 1}/{len(replacements)}] " if len(replacements) > 1 else ""
            report = (
                f"{prefix}Applied ({result.method}, confidence={result.confidence:.0%}, "
                f"verified={result.verified}) to {path}"
            )
            if preview:
                report += f"\n{preview}"
            reports.append(report)

        if failed is not None:
            idx, _s, err = failed
            content = target.read_text(encoding="utf-8", errors="replace")
            hints = self._hint_context(_s, content)
            label = f" at step {idx}/{len(replacements)}" if len(replacements) > 1 else ""
            msg = f"Edit failed{label}: {err}"
            if hints:
                msg += f"\nNear-miss context (possible intent):\n{hints}"
            return msg

        return "\n\n".join(reports)

    def _hint_context(self, search: str, content: str) -> str:
        """Return nearby lines that resemble the failed search for context hints."""
        if not content:
            return ""
        sig = search.strip()
        if not sig:
            return ""
        scored: list[tuple[float, int, str]] = []
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            if sig in line:
                ratio = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, sig, stripped).ratio()
            if ratio > 0.35:
                scored.append((ratio, i, stripped[:100]))
        scored.sort(key=lambda t: t[0], reverse=True)
        return "\n".join(f"  line {ln}: {text}" for _, ln, text in scored[:3])

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class EditLinesTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self._engine = EditEngine()

    @property
    def name(self) -> str:
        return "edit_lines"

    @property
    def description(self) -> str:
        return (
            "Replace a range of lines in a file. "
            "Provide start line (1-indexed), end line (1-indexed), and new content. "
            "Automatically verifies syntax after edit."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "start": {"type": "integer", "description": "Start line (1-indexed)"},
                "end": {"type": "integer", "description": "End line (1-indexed, inclusive)"},
                "content": {"type": "string", "description": "New content for those lines"},
            },
            "required": ["path", "start", "end", "content"],
        }

    async def run(self, path: str, start: int, end: int, content: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            return f"Error: file not found: {path}"
        result = self._engine.apply_line_edit(target, start, end, content)
        if result.success:
            return f"Replaced lines {start}-{end} in {path} ({result.method}, verified={result.verified})"
        return f"Edit failed: {result.error}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class AiderEditTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self._engine = EditEngine()

    @property
    def name(self) -> str:
        return "aider_edit"

    @property
    def description(self) -> str:
        return (
            "Apply one or more SEARCH/REPLACE blocks in Aider format. "
            "Use this for complex multi-file edits. Format:\n"
            "path/to/file.py\n<<<<<<< SEARCH\nold code\n=======\nnew code\n>>>>>>> REPLACE"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "One or more SEARCH/REPLACE blocks with file paths",
                },
            },
            "required": ["content"],
        }

    async def run(self, content: str) -> str:
        results = self._engine.apply_aider_blocks(content, self.repo_path)
        if not results:
            return "No valid SEARCH/REPLACE blocks found. Use format: path/file.py\\n<<<<<<< SEARCH\\nold\\n=======\\nnew\\n>>>>>>> REPLACE"
        parts = []
        for r in results:
            if r.success:
                parts.append(f"OK: {r.path} via {r.method}")
            else:
                parts.append(f"FAIL: {r.path}: {r.error}")
        return "\n".join(parts)
