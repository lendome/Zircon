"""Semantic navigation tools: get_function_body, find_references, get_symbol_definition, get_function_dependencies.

Thin async wrappers over ``parsers.symbol_nav`` (pure/sync logic). These give
the agent symbol-oriented code access instead of blind line-range guessing:

- ``get_symbol_definition`` — where is this symbol defined (file, lines, kind)
- ``get_function_body`` — the full source of a function/method, numbered
- ``find_references`` — every word-boundary usage of a symbol, grouped by file
- ``get_function_dependencies`` — what does this function call, resolved to file:line
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import Tool
from ..parsers.symbol_nav import (
    Definition,
    extract_body,
    extract_dependencies,
    find_definitions,
    find_references,
    get_ast_range,
    get_callers,
)


def _format_definition(d: Definition) -> str:
    parent = f" (in {d.parent})" if d.parent else ""
    sig = f"\n    {d.signature}" if d.signature else ""
    return f"{d.path}:{d.line}-{d.end_line}: {d.kind} {d.name}{parent}{sig}"


class GetSymbolDefinitionTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_symbol_definition"

    @property
    def description(self) -> str:
        return (
            "Locate the definition of a function, class, method, or type by "
            "name. Returns file path, exact start/end lines, and kind. Use "
            "this INSTEAD of grep when you know the symbol name — it "
            "understands code structure and ignores comments/strings."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol name (e.g. 'parse_config' or 'Config.load')"},
                "path": {"type": "string", "description": "Optional file or directory to scope the search"},
            },
            "required": ["symbol"],
        }

    async def run(self, symbol: str, path: str | None = None) -> str:
        definitions = await asyncio.to_thread(
            find_definitions, self.repo_path, symbol, "any", path
        )
        if not definitions:
            scope = f" under {path}" if path else ""
            return f"No definition found for '{symbol}'{scope}."
        if len(definitions) == 1:
            return _format_definition(definitions[0])
        lines = [f"{len(definitions)} definitions for '{symbol}':"]
        lines.extend(f"  {_format_definition(d)}" for d in definitions[:20])
        return "\n".join(lines)


class GetFunctionBodyTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_function_body"

    @property
    def description(self) -> str:
        return (
            "Read the complete source of one function or method BY NAME, with "
            "line numbers. Use this INSTEAD of read_file with guessed line "
            "ranges — give it the symbol name and it finds the exact start and "
            "end of the body (handles nesting, not just a fixed window)."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Function/method name (e.g. 'parse_config' or 'Config.load')"},
                "path": {"type": "string", "description": "File to read from (required when the name is ambiguous)"},
                "max_lines": {"type": "integer", "description": "Max body lines returned (default: 400)"},
            },
            "required": ["symbol"],
        }

    async def run(self, symbol: str, path: str | None = None, max_lines: int = 400) -> str:
        max_lines = max(10, min(2000, int(max_lines)))
        result = await asyncio.to_thread(extract_body, self.repo_path, symbol, path, max_lines)
        if not result["ok"]:
            candidates = result.get("candidates") or []
            if candidates:
                lines = [result["error"], ""]
                lines.extend(f"  {_format_definition(d)}" for d in candidates[:20])
                return "\n".join(lines)
            return result["error"]
        header = (
            f"{result['path']}:{result['line']}-{result['end_line']}: "
            f"{result['kind']} {result['name']}"
        )
        if result.get("truncated"):
            header += f"  (truncated at {max_lines} lines; full body ends at line {result['full_end_line']})"
        return f"{header}\n{result['body']}"


class FindReferencesTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "find_references"

    @property
    def description(self) -> str:
        return (
            "Find every usage (call site / read) of a symbol across the "
            "codebase. Word-boundary match, so 'run' does not match 'runner'. "
            "Definition lines are listed separately from usages. Use this to "
            "understand impact before editing a function's signature or "
            "behavior."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol name to find usages of"},
                "path": {"type": "string", "description": "Optional file or directory to scope the search"},
            },
            "required": ["symbol"],
        }

    async def run(self, symbol: str, path: str | None = None) -> str:
        result = await asyncio.to_thread(find_references, self.repo_path, symbol, path)
        if not result["ok"]:
            return result["error"]

        definitions: list[Definition] = result["definitions"]
        references = result["references"]

        lines: list[str] = []
        if definitions:
            lines.append(f"Definition{'s' if len(definitions) > 1 else ''}:")
            for d in definitions[:10]:
                lines.append(f"  {_format_definition(d)}")
        if not references:
            lines.append(f"\nNo references to '{symbol}' found" + (f" under {path}." if path else "."))
            return "\n".join(lines)

        lines.append(f"\n{len(references)} reference{'s' if len(references) != 1 else ''}"
                     + (" (capped)" if result.get("truncated") else "") + ":")
        # Group by file for scannability.
        by_file: dict[str, list[str]] = {}
        for ref in references:
            by_file.setdefault(ref.path, []).append(f"  {ref.line}: {ref.text}")
        for file, rows in by_file.items():
            lines.append(f"{file}:")
            lines.extend(rows[:15])
        return "\n".join(lines)


class GetFunctionDependenciesTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_function_dependencies"

    @property
    def description(self) -> str:
        return (
            "Map a function's call graph: list every function/method it "
            "CALLS, each resolved to its definition (file:line). Use this "
            "INSTEAD of reading whole files to trace what a function "
            "invokes — then open only the dependencies that matter with "
            "get_function_body. Builtins/external calls are listed "
            "separately as unresolved."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Function/method name (e.g. 'materialize' or 'Engine.run')"},
                "path": {"type": "string", "description": "File to read from (required when the name is ambiguous)"},
            },
            "required": ["symbol"],
        }

    async def run(self, symbol: str, path: str | None = None) -> str:
        result = await asyncio.to_thread(
            extract_dependencies, self.repo_path, symbol, path
        )
        if not result["ok"]:
            candidates = result.get("candidates") or []
            if candidates:
                lines = [result["error"], ""]
                lines.extend(f"  {_format_definition(d)}" for d in candidates[:20])
                return "\n".join(lines)
            return result["error"]

        header = (
            f"{result['path']}:{result['line']}-{result['end_line']}: "
            f"{result['kind']} {result['name']}"
        )
        resolved = result["resolved"]
        unresolved = result["unresolved"]
        lines = [header]
        if resolved:
            lines.append(f"Calls (resolved to definitions){' (capped)' if result.get('truncated') else ''}:")
            for r in resolved:
                qualified = f" ({r['qualified']})" if r["qualified"] != r["name"] else ""
                lines.append(f"  {r['name']} → {r['path']}:{r['line']}{qualified}")
        else:
            lines.append("Calls (resolved to definitions): none found in this repo")
        if unresolved:
            lines.append("Unresolved/external: " + ", ".join(unresolved))
        if not resolved and not unresolved:
            lines.append("This function makes no detectable calls.")
        return "\n".join(lines)


class GetCallersTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_callers"

    @property
    def description(self) -> str:
        return (
            "Reverse call graph: list every function/method in the repo that "
            "CALLS a given symbol, each resolved to its definition (file:line). "
            "Use this to answer 'who calls X?' before refactoring or deleting "
            "a function — then open only the callers that matter with "
            "get_function_body. The complement of get_function_dependencies "
            "(which lists what a function calls)."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Function/method name (e.g. 'materialize' or 'Engine.run')"},
                "path": {"type": "string", "description": "File to resolve the target in (required when the name is ambiguous)"},
            },
            "required": ["symbol"],
        }

    async def run(self, symbol: str, path: str | None = None) -> str:
        result = await asyncio.to_thread(get_callers, self.repo_path, symbol, path)
        if not result["ok"]:
            candidates = result.get("candidates") or []
            if candidates:
                lines = [result["error"], ""]
                lines.extend(f"  {_format_definition(d)}" for d in candidates[:20])
                return "\n".join(lines)
            return result["error"]

        target: Definition = result["target"]
        callers = result["callers"]
        lines = [f"Target: {target.path}:{target.line} {target.kind} {target.name}"]
        if not callers:
            lines.append(f"No callers of '{symbol}' found in this repo.")
            return "\n".join(lines)

        lines.append(
            f"\nCallers ({len(callers)}){' (capped)' if result.get('truncated') else ''}:"
        )
        by_file: dict[str, list[str]] = {}
        for c in callers:
            by_file.setdefault(c["path"], []).append(
                f"  {c['line']}: {c['kind']} {c['name']}"
            )
        for file, rows in by_file.items():
            lines.append(f"{file}:")
            lines.extend(rows[:15])
        return "\n".join(lines)


class GetAstRangeTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "get_ast_range"

    @property
    def description(self) -> str:
        return (
            "Expand a line range to its enclosing AST block: give it a file "
            "and a start line (optionally an end line) from a grep/read hit, "
            "and it returns the smallest enclosing function/class/if/for/while/"
            "try block (Python uses real AST; Go/JS/TS/Rust use the enclosing "
            "definition) with its full source, line numbers, kind, and the "
            "enclosing scope chain. Use this to understand control flow and "
            "variable scope around a hit instead of reading whole files."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File containing the line range"},
                "start_line": {"type": "integer", "description": "1-indexed start line of the range to expand"},
                "end_line": {"type": "integer", "description": "Optional 1-indexed end line (defaults to start_line)"},
                "max_lines": {"type": "integer", "description": "Max block lines returned (default: 600)"},
            },
            "required": ["path", "start_line"],
        }

    async def run(self, path: str, start_line: int, end_line: int | None = None, max_lines: int = 600) -> str:
        result = await asyncio.to_thread(
            get_ast_range, self.repo_path, path, start_line, end_line, max_lines
        )
        if not result["ok"]:
            return result["error"]
        header = f"{result['path']}:{result['start_line']}-{result['end_line']}: {result['kind']}"
        if result.get("name"):
            header += f" {result['name']}"
        if result.get("expanded"):
            header += (
                f"  (expanded from {result['requested_start']}"
                f"-{result['requested_end']})"
            )
        if result.get("truncated"):
            header += f"  (truncated at {max_lines} lines)"
        if result.get("enclosing_scope"):
            header += f"\n  scope: {result['enclosing_scope']}"
        return f"{header}\n{result['body']}"
