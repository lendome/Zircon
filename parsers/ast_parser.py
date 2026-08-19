from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from ..core.exclusions import is_excluded


# Lightweight regex extractors for non-Python languages. Top-level symbols
# only — the goal is a fast outline for orientation, not a full parse.
_GO_METHOD = re.compile(r"^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)")
_GO_FUNC = re.compile(r"^func\s+(\w+)")
_GO_TYPE = re.compile(r"^type\s+(\w+)\s+(struct|interface|func|\w+)")

_JS_FUNC = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)")
_JS_CLASS = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)")
_JS_INTERFACE = re.compile(r"^(?:export\s+)?interface\s+(\w+)")
_JS_TYPE = re.compile(r"^(?:export\s+)?type\s+(\w+)\s*=")
_JS_ARROW = re.compile(
    r"^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>"
)

_RS_FN = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)")
_RS_TYPE = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+(\w+)")
_RS_IMPL = re.compile(r"^impl(?:<[^>]*>)?\s+(?:\w+\s+for\s+)?(\w+)")

SUPPORTED_SUFFIXES = (
    ".py", ".go", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".rs",
)


class ASTParser:
    def extract_symbols(self, file_path: Path) -> list[dict[str, Any]]:
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            return self._parse_python(file_path)
        if suffix == ".go":
            return self._parse_regex(file_path, self._match_go)
        if suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            return self._parse_regex(file_path, self._match_js)
        if suffix == ".rs":
            return self._parse_regex(file_path, self._match_rust)
        return []

    def _parse_python(self, file_path: Path) -> list[dict[str, Any]]:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, Exception):
            return []

        symbols: list[dict[str, Any]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({
                    "name": node.name,
                    "kind": "function",
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "args": [a.arg for a in node.args.args if a.arg != "self"],
                    "parent": None,
                })
            elif isinstance(node, ast.ClassDef):
                symbols.append({
                    "name": node.name,
                    "kind": "class",
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "args": [],
                    "parent": None,
                })
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append({
                            "name": f"{node.name}.{item.name}",
                            "kind": "method",
                            "line": item.lineno,
                            "end_line": getattr(item, "end_lineno", item.lineno),
                            "args": [a.arg for a in item.args.args if a.arg != "self"],
                            "parent": node.name,
                        })

        return symbols

    def _parse_regex(self, file_path: Path, matcher) -> list[dict[str, Any]]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        symbols: list[dict[str, Any]] = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            sym = matcher(line)
            if sym is not None:
                sym["line"] = lineno
                sym["end_line"] = lineno
                sym.setdefault("args", [])
                sym.setdefault("parent", None)
                symbols.append(sym)
        return symbols

    @staticmethod
    def _match_go(line: str) -> dict[str, Any] | None:
        m = _GO_METHOD.match(line)
        if m:
            return {"name": f"{m.group(1)}.{m.group(2)}", "kind": "method", "parent": m.group(1)}
        m = _GO_FUNC.match(line)
        if m:
            return {"name": m.group(1), "kind": "function"}
        m = _GO_TYPE.match(line)
        if m:
            kind = "class" if m.group(2) in ("struct", "interface") else "symbol"
            return {"name": m.group(1), "kind": kind}
        return None

    @staticmethod
    def _match_js(line: str) -> dict[str, Any] | None:
        m = _JS_FUNC.match(line)
        if m:
            return {"name": m.group(1), "kind": "function"}
        m = _JS_CLASS.match(line)
        if m:
            return {"name": m.group(1), "kind": "class"}
        m = _JS_INTERFACE.match(line) or _JS_TYPE.match(line)
        if m:
            return {"name": m.group(1), "kind": "class"}
        m = _JS_ARROW.match(line)
        if m:
            return {"name": m.group(1), "kind": "function"}
        return None

    @staticmethod
    def _match_rust(line: str) -> dict[str, Any] | None:
        m = _RS_FN.match(line)
        if m:
            return {"name": m.group(1), "kind": "function"}
        m = _RS_TYPE.match(line)
        if m:
            return {"name": m.group(1), "kind": "class"}
        m = _RS_IMPL.match(line)
        if m:
            return {"name": m.group(1), "kind": "class"}
        return None

    def get_repo_map(self, repo_path: Path, max_files: int = 100) -> str:
        lines = []
        count = 0
        candidates: list[Path] = []
        for suffix in SUPPORTED_SUFFIXES:
            candidates.extend(repo_path.rglob(f"*{suffix}"))
        for src_file in sorted(candidates):
            if any(part.startswith(".") for part in src_file.relative_to(repo_path).parts):
                continue
            if is_excluded(src_file):
                continue
            if count >= max_files:
                break
            rel = src_file.relative_to(repo_path)
            symbols = self.extract_symbols(src_file)
            if not symbols:
                lines.append(f"{rel}: (no symbols)")
            else:
                sym_strs = []
                for sym in symbols:
                    indent = "  " if sym.get("parent") else ""
                    sym_strs.append(f"{sym['line']}:{indent}{sym['kind']} {sym['name']}")
                lines.append(f"{rel}:\n  " + "\n  ".join(sym_strs))
            count += 1
        return "\n".join(lines)
