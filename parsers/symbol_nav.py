"""Semantic code navigation: definition lookup, body extraction, references.

Replaces blind line-range guessing (read_file(start=1135, end=1320)) and raw
regex greps with symbol-oriented queries. Built on top of the existing
ASTParser (Python via ``ast`` with real end_lineno; Go/JS/TS/Rust via regex
signatures) plus a lightweight brace-matcher that computes approximate body
end lines for the regex languages.

The public functions are pure and synchronous so they are trivially testable.
A real LSP backend can be slotted behind the same interface later without
changing the tool schemas.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ast_parser import ASTParser, SUPPORTED_SUFFIXES

_MAX_BODY_LINES = 400
_MAX_REFERENCES = 50
_MAX_SEARCH_FILES = 4000
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".zircon-code",
    ".venv", "venv", "env", "dist", "build", "target", ".idea", ".vscode",
}


@dataclass
class Definition:
    path: str
    line: int
    end_line: int
    kind: str
    name: str
    parent: str | None = None
    signature: str = ""


@dataclass
class Reference:
    path: str
    line: int
    text: str


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------


def _iter_source_files(repo: Path, scope: Path | None = None) -> list[Path]:
    base = scope if scope is not None else repo
    if base.is_file():
        return [base] if base.suffix.lower() in SUPPORTED_SUFFIXES else []
    out: list[Path] = []
    for p in sorted(base.rglob("*")):
        if len(out) >= _MAX_SEARCH_FILES:
            break
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            rel = p.relative_to(repo)
        except ValueError:
            rel = p
        # Skip hidden dirs (.git, .zircon-code) and heavy vendor dirs.
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Brace matching for regex-parsed languages
# ---------------------------------------------------------------------------

_LINE_COMMENT = re.compile(r"//|#")
_BLOCK_COMMENT_OPEN = re.compile(r"/\*")
_BLOCK_COMMENT_CLOSE = re.compile(r"\*/")


def _strip_strings_and_comments(line: str, in_block_comment: bool) -> tuple[str, bool]:
    """Remove string literals and comments from *line* for brace counting.

    Best-effort: handles 'x', "x", `x` (JS/Go templates), // and /* */.
    Python triple-quoted strings are handled by the ast path, not here.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    in_string: str | None = None
    while i < n:
        ch = line[i]
        if in_block_comment:
            end = line.find("*/", i)
            if end == -1:
                return "".join(out), True
            i = end + 2
            in_block_comment = False
            continue
        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            break  # rest is a line comment
        if ch == "/" and i + 1 < n and line[i + 1] == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "#":
            break  # python/bash-style line comment (harmless for Go/JS)
        out.append(ch)
        i += 1
    return "".join(out), in_block_comment


def _find_body_end(lines: list[str], start_idx: int) -> int:
    """Given 0-based *start_idx* of a definition line, return 1-based end line.

    Counts braces from the start line; if no opening brace appears (e.g. a
    Python-style signature or an interface method), returns the start line.
    """
    depth = 0
    seen_open = False
    in_block = False
    for idx in range(start_idx, len(lines)):
        stripped, in_block = _strip_strings_and_comments(lines[idx], in_block)
        for ch in stripped:
            if ch == "{":
                depth += 1
                seen_open = True
            elif ch == "}":
                depth -= 1
                if seen_open and depth <= 0:
                    return idx + 1
    # No braces at all (signature-only, arrow function, interface method):
    # the body is just the definition line. Unbalanced braces (broken code):
    # best guess is the file end.
    if not seen_open:
        return start_idx + 1
    return len(lines)


# ---------------------------------------------------------------------------
# Definition lookup
# ---------------------------------------------------------------------------


def find_definitions(
    repo_path: str | Path,
    symbol: str,
    kind: str = "any",
    scope: str | None = None,
) -> list[Definition]:
    """Find definitions of *symbol* across the repo (or under *scope*).

    Matching: exact name match first; falls back to substring matches (like
    find_symbols) only when there are no exact hits. Methods match both
    ``method`` and ``Class.method`` forms.
    """
    repo = Path(repo_path).resolve()
    scope_path = (repo / scope).resolve() if scope else None
    if scope_path is not None and not scope_path.exists():
        return []

    parser = ASTParser()
    exact: list[Definition] = []
    partial: list[Definition] = []
    needle = symbol.lower()

    for file in _iter_source_files(repo, scope_path):
        try:
            symbols = parser.extract_symbols(file)
        except Exception:
            continue
        if not symbols:
            continue
        try:
            rel = str(file.relative_to(repo))
        except ValueError:
            rel = str(file)
        # Compute body ends lazily only for regex languages (end_line == line).
        lines_cache: list[str] | None = None
        for sym in symbols:
            name = sym["name"]
            short = name.rsplit(".", 1)[-1]
            if kind != "any" and sym.get("kind") != kind:
                continue
            is_exact = short.lower() == needle or name.lower() == needle
            is_partial = not is_exact and needle in short.lower()
            if not is_exact and not is_partial:
                continue
            line = int(sym["line"])
            end = int(sym.get("end_line") or line)
            if end <= line and file.suffix.lower() != ".py":
                if lines_cache is None:
                    try:
                        lines_cache = file.read_text(encoding="utf-8", errors="replace").splitlines()
                    except Exception:
                        lines_cache = []
                if lines_cache:
                    end = _find_body_end(lines_cache, line - 1)
            definition = Definition(
                path=rel,
                line=line,
                end_line=end,
                kind=sym.get("kind", "symbol"),
                name=name,
                parent=sym.get("parent"),
                signature=_signature_line(file, line),
            )
            (exact if is_exact else partial).append(definition)

    return exact or partial


def _signature_line(file: Path, line: int) -> str:
    try:
        with file.open(encoding="utf-8", errors="replace") as fh:
            for i, text in enumerate(fh, 1):
                if i == line:
                    return text.strip()[:200]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------


def extract_body(
    repo_path: str | Path,
    symbol: str,
    path: str | None = None,
    max_lines: int = _MAX_BODY_LINES,
) -> dict[str, Any]:
    """Extract the full source body of *symbol*.

    Returns a dict with either the body payload or an ambiguity/error note::

        {"ok": True, "path": ..., "line": ..., "end_line": ...,
         "kind": ..., "body": "...", "truncated": bool}
        {"ok": False, "error": "...", "candidates": [Definition, ...]}
    """
    repo = Path(repo_path).resolve()
    definitions = find_definitions(repo, symbol, scope=path)
    if not definitions:
        return {"ok": False, "error": f"No definition found for '{symbol}'", "candidates": []}

    if path:
        norm = str(Path(path)).replace("/", "\\").lower().lstrip(".\\")
        scoped = [
            d for d in definitions
            if d.path.replace("/", "\\").lower() == norm
            or d.path.replace("/", "\\").lower().endswith("\\" + norm)
        ]
        if scoped:
            definitions = scoped

    if len(definitions) > 1:
        return {
            "ok": False,
            "error": (
                f"'{symbol}' is ambiguous — {len(definitions)} definitions. "
                f"Pass path= to disambiguate."
            ),
            "candidates": definitions,
        }

    d = definitions[0]
    file = repo / d.path
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"ok": False, "error": f"Could not read {d.path}: {e}", "candidates": []}

    start = max(1, d.line)
    end = min(len(lines), max(d.end_line, start))
    truncated = False
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
        truncated = True
    numbered = [f"{i:>5}: {lines[i - 1]}" for i in range(start, end + 1)]
    return {
        "ok": True,
        "path": d.path,
        "line": start,
        "end_line": end,
        "full_end_line": d.end_line,
        "kind": d.kind,
        "name": d.name,
        "body": "\n".join(numbered),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Reference search
# ---------------------------------------------------------------------------


def find_references(
    repo_path: str | Path,
    symbol: str,
    scope: str | None = None,
    max_results: int = _MAX_REFERENCES,
) -> dict[str, Any]:
    """Find word-boundary references to *symbol* across source files.

    Excludes the definition lines themselves (reported separately). Uses a
    plain regex walk — deterministic and dependency-free.
    """
    repo = Path(repo_path).resolve()
    scope_path = (repo / scope).resolve() if scope else None
    if scope_path is not None and not scope_path.exists():
        return {"ok": False, "error": f"scope not found: {scope}", "references": [], "definitions": []}

    short = symbol.rsplit(".", 1)[-1]
    try:
        pattern = re.compile(rf"\b{re.escape(short)}\b")
    except re.error as e:
        return {"ok": False, "error": f"invalid symbol: {e}", "references": [], "definitions": []}

    definitions = find_definitions(repo, short)
    def_lines = {(d.path, d.line) for d in definitions}

    references: list[Reference] = []
    for file in _iter_source_files(repo, scope_path):
        if len(references) >= max_results:
            break
        try:
            rel = str(file.relative_to(repo))
        except ValueError:
            rel = str(file)
        try:
            with file.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if not pattern.search(line):
                        continue
                    if (rel, i) in def_lines:
                        continue
                    references.append(Reference(path=rel, line=i, text=line.strip()[:200]))
                    if len(references) >= max_results:
                        break
        except Exception:
            continue

    return {
        "ok": True,
        "references": references,
        "definitions": definitions,
        "truncated": len(references) >= max_results,
    }


# ---------------------------------------------------------------------------
# Dependency extraction (call-graph mapping)
# ---------------------------------------------------------------------------

_MAX_DEPENDENCIES = 30
_MAX_UNRESOLVED = 20

# Called names that are never interesting dependencies: language builtins,
# keywords, and ubiquitous stdlib constructors across the supported languages.
_CALL_STOPLIST = frozenset({
    # Python builtins / keywords
    "print", "len", "range", "str", "int", "float", "bool", "list", "dict",
    "set", "tuple", "type", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "iter", "next", "enumerate", "zip", "map",
    "filter", "sorted", "reversed", "min", "max", "sum", "abs", "round",
    "open", "repr", "id", "hash", "callable", "vars", "dir", "any", "all",
    "ord", "chr", "hex", "bin", "oct", "format", "input", "bytes", "bytearray",
    "frozenset", "complex", "slice", "object", "property", "staticmethod",
    "classmethod", "Exception", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "RuntimeError", "StopIteration",
    "NotImplementedError", "OSError", "IOError", "TimeoutError", "self",
    "cls", "True", "False", "None",
    # Go builtins / keywords
    "make", "new", "append", "copy", "delete", "panic", "recover", "close",
    "cap", "error", "string", "byte", "rune", "func", "chan", "select",
    "struct", "interface", "package", "const", "nil", "true", "false",
    # JS/TS builtins / keywords
    "require", "console", "JSON", "Math", "Object", "Array", "String",
    "Number", "Boolean", "Promise", "Set", "Map", "WeakMap", "WeakSet",
    "Symbol", "Date", "RegExp", "Error", "parseInt", "parseFloat", "isNaN",
    "undefined", "null", "function", "typeof", "instanceof", "this",
    # Rust builtins / macros
    "println", "vec", "format", "assert", "assert_eq", "assert_ne",
    "debug_assert", "Some", "None", "Ok", "Err", "Box", "Vec", "fn",
})

_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def _python_callees_of_node(target: ast.AST) -> set[str]:
    """Collect names called inside a Python function/method node.

    Shared core used by ``extract_dependencies`` (forward call graph) and
    ``get_callers`` (reverse call graph). The callee name is the final
    attribute for attribute calls (``obj.materialize()`` -> "materialize") or
    the bare id for name calls. Parameters and names bound inside the body are
    excluded so local callables are not mistaken for repo-level dependencies.
    """
    bound: set[str] = set()
    args = getattr(target, "args", None)
    if args is not None:
        bound.update(a.arg for a in list(args.args) + list(args.kwonlyargs))
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)

    calls: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.alias):
            bound.add(node.asname or node.name.split(".", 1)[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)

    calls -= bound
    calls -= _CALL_STOPLIST
    own = getattr(target, "name", "")
    if own:
        calls.discard(own)
    return calls


def _python_call_names(source: str, d: Definition) -> set[str]:
    """Collect names called inside the Python function defined at *d*.

    Uses a real ``ast`` parse. The callee name is the final attribute for
    attribute calls (``obj.materialize()`` -> "materialize") or the bare id
    for name calls. Parameters and names bound inside the body are excluded
    so local callables are not mistaken for repo-level dependencies.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    short = d.name.rsplit(".", 1)[-1]
    target: ast.AST | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.lineno == d.line
            and node.name == short
        ):
            target = node
            break
    if target is None:
        return set()
    return _python_callees_of_node(target)


def _regex_call_names(source: str, d: Definition) -> set[str]:
    """Best-effort call extraction for the regex-parsed languages."""
    lines = source.splitlines()
    body = lines[d.line - 1: max(d.end_line, d.line)]
    calls: set[str] = set()
    for line in body:
        for m in _CALL_RE.finditer(line):
            calls.add(m.group(1))
    calls -= _CALL_STOPLIST
    calls.discard(d.name.rsplit(".", 1)[-1])
    return calls


def _build_definition_index(repo: Path) -> dict[str, list[Definition]]:
    """One-pass name -> definitions index over the whole repo.

    Resolving N callees via N separate find_definitions walks would re-parse
    every file N times; this pays the scan cost once. End lines are not
    computed (definitions are only reported, never body-extracted), so this
    stays cheap for the regex languages.
    """
    parser = ASTParser()
    index: dict[str, list[Definition]] = {}
    for file in _iter_source_files(repo):
        try:
            symbols = parser.extract_symbols(file)
        except Exception:
            continue
        if not symbols:
            continue
        try:
            rel = str(file.relative_to(repo))
        except ValueError:
            rel = str(file)
        for sym in symbols:
            name = str(sym.get("name", ""))
            if not name:
                continue
            definition = Definition(
                path=rel,
                line=int(sym.get("line", 1)),
                end_line=int(sym.get("line", 1)),
                kind=sym.get("kind", "symbol"),
                name=name,
                parent=sym.get("parent"),
            )
            index.setdefault(name.rsplit(".", 1)[-1].lower(), []).append(definition)
    return index


def extract_dependencies(
    repo_path: str | Path,
    symbol: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Extract the functions/methods that *symbol* calls (its dependencies).

    Each callee is resolved against a repo-wide definition index so the agent
    can jump straight to the dependency that matters instead of reading whole
    files to trace the call graph.

    Returns::

        {"ok": True, "path": ..., "line": ..., "end_line": ..., "name": ...,
         "resolved": [{"name", "path", "line", "kind"}, ...],
         "unresolved": ["name", ...]}
        {"ok": False, "error": ..., "candidates": [Definition, ...]}
    """
    repo = Path(repo_path).resolve()
    definitions = find_definitions(repo, symbol, scope=path)
    if not definitions:
        return {"ok": False, "error": f"No definition found for '{symbol}'", "candidates": []}

    if path:
        norm = str(Path(path)).replace("/", "\\").lower().lstrip(".\\")
        scoped = [
            d for d in definitions
            if d.path.replace("/", "\\").lower() == norm
            or d.path.replace("/", "\\").lower().endswith("\\" + norm)
        ]
        if scoped:
            definitions = scoped

    if len(definitions) > 1:
        return {
            "ok": False,
            "error": (
                f"'{symbol}' is ambiguous — {len(definitions)} definitions. "
                f"Pass path= to disambiguate."
            ),
            "candidates": definitions,
        }

    d = definitions[0]
    file = repo / d.path
    try:
        source = file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"Could not read {d.path}: {e}", "candidates": []}

    if file.suffix.lower() == ".py":
        call_names = _python_call_names(source, d)
    else:
        call_names = _regex_call_names(source, d)

    resolved: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if call_names:
        index = _build_definition_index(repo)
        own = (d.path, d.line)
        for callee in sorted(call_names):
            defs = index.get(callee.lower())
            if not defs:
                if len(unresolved) < _MAX_UNRESOLVED:
                    unresolved.append(callee)
                continue
            # Prefer a definition outside the calling function itself.
            pick = next(
                (x for x in defs if (x.path, x.line) != own),
                defs[0],
            )
            if len(resolved) < _MAX_DEPENDENCIES:
                resolved.append({
                    "name": callee,
                    "path": pick.path,
                    "line": pick.line,
                    "kind": pick.kind,
                    "qualified": pick.name,
                })

    return {
        "ok": True,
        "path": d.path,
        "line": d.line,
        "end_line": d.end_line,
        "name": d.name,
        "kind": d.kind,
        "resolved": resolved,
        "unresolved": unresolved,
        "truncated": bool(call_names)
        and len(resolved) + len(unresolved) < len(call_names),
    }


# ---------------------------------------------------------------------------
# Reverse call graph (who calls X)
# ---------------------------------------------------------------------------

_MAX_CALLERS = 50


def get_callers(
    repo_path: str | Path,
    symbol: str,
    path: str | None = None,
) -> dict[str, Any]:
    """Find every function/method in the repo that calls *symbol*.

    The reverse of ``extract_dependencies``: resolves the target definition,
    then walks every other function/method, extracts its callees (reusing the
    same Python-AST / regex-Call extraction), and reports those whose callee
    set contains the target's short name. Each caller is resolved to
    file:line so the agent can jump straight to it instead of grepping.

    Returns::

        {"ok": True, "symbol": ..., "target": Definition,
         "callers": [{"path","line","end_line","kind","name"}, ...],
         "truncated": bool}
        {"ok": False, "error": ..., "candidates": [Definition, ...]}
    """
    repo = Path(repo_path).resolve()
    definitions = find_definitions(repo, symbol, scope=path)
    if not definitions:
        return {"ok": False, "error": f"No definition found for '{symbol}'", "candidates": []}

    if path:
        norm = str(Path(path)).replace("/", "\\").lower().lstrip(".\\")
        scoped = [
            d for d in definitions
            if d.path.replace("/", "\\").lower() == norm
            or d.path.replace("/", "\\").lower().endswith("\\" + norm)
        ]
        if scoped:
            definitions = scoped

    if len(definitions) > 1:
        return {
            "ok": False,
            "error": (
                f"'{symbol}' is ambiguous — {len(definitions)} definitions. "
                f"Pass path= to disambiguate."
            ),
            "candidates": definitions,
        }

    target = definitions[0]
    target_short = target.name.rsplit(".", 1)[-1]
    target_key = target_short.lower()
    own_loc = (target.path, target.line)

    parser = ASTParser()
    callers: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for file in _iter_source_files(repo):
        if len(callers) >= _MAX_CALLERS:
            break
        try:
            symbols = parser.extract_symbols(file)
        except Exception:
            continue
        if not symbols:
            continue
        try:
            rel = str(file.relative_to(repo))
        except ValueError:
            rel = str(file)
        # Only files that even mention the target name can be callers; this
        # avoids reading/parsing the whole repo when the symbol is localized.
        try:
            source = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if target_key not in source.lower():
            continue
        lines = source.splitlines()
        is_py = file.suffix.lower() == ".py"

        tree: ast.AST | None = None
        if is_py:
            try:
                tree = ast.parse(source)
            except (SyntaxError, ValueError):
                tree = None
            if tree is not None:
                # Map top-level / method function lines to their ast nodes so
                # we can reuse a single parse per file (one ast.walk for the
                # node lookup, not a re-parse per symbol).
                fn_nodes: dict[int, ast.AST] = {}
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn_nodes.setdefault(node.lineno, node)

        for sym in symbols:
            if sym.get("kind") not in ("function", "method"):
                continue
            s = int(sym.get("line", 1))
            if (rel, s) == own_loc:
                continue
            if (rel, s) in seen:
                continue
            e = int(sym.get("end_line") or s)
            if e <= s and not is_py:
                e = _find_body_end(lines, s - 1)
            d = Definition(
                path=rel, line=s, end_line=e,
                kind=sym.get("kind", "function"),
                name=str(sym.get("name", "")),
                parent=sym.get("parent"),
            )
            if is_py:
                if tree is None:
                    continue
                node = fn_nodes.get(s)
                if node is None:
                    # Nested defs are not in the top-level symbol index; skip
                    # them rather than mis-attributing via the enclosing func.
                    continue
                callees = _python_callees_of_node(node)
            else:
                callees = _regex_call_names(source, d)
            if target_key in {c.lower() for c in callees}:
                seen.add((rel, s))
                callers.append({
                    "path": rel, "line": s, "end_line": e,
                    "kind": d.kind, "name": d.name,
                })
                if len(callers) >= _MAX_CALLERS:
                    break

    return {
        "ok": True,
        "symbol": symbol,
        "target": target,
        "callers": callers,
        "truncated": len(callers) >= _MAX_CALLERS,
    }


# ---------------------------------------------------------------------------
# AST-aware range expansion (get the enclosing block for a line range)
# ---------------------------------------------------------------------------

_MAX_AST_RANGE_LINES = 600

# Python statement nodes that introduce a lexical block / scope worth
# expanding a partial selection to. The smallest-by-span containing node of
# these types is returned (tightest enclosing block).
_PY_BLOCK_TYPES: tuple[type, ...] = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.If, ast.For, ast.AsyncFor, ast.While,
    ast.With, ast.AsyncWith, ast.Try, ast.ExceptHandler,
)
if hasattr(ast, "Match"):  # 3.10+
    _PY_BLOCK_TYPES = (*_PY_BLOCK_TYPES, ast.Match)  # type: ignore[arg-type]

_PY_KIND_LABELS: dict[type, str] = {
    ast.FunctionDef: "function",
    ast.AsyncFunctionDef: "async_function",
    ast.ClassDef: "class",
    ast.If: "if_block",
    ast.For: "for_block",
    ast.AsyncFor: "async_for_block",
    ast.While: "while_block",
    ast.With: "with_block",
    ast.AsyncWith: "async_with_block",
    ast.Try: "try_block",
    ast.ExceptHandler: "except_block",
}


def _node_span(node: ast.AST) -> tuple[int, int] | None:
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if start is None or end is None:
        return None
    return start, end


def _python_best_block(tree: ast.AST, start_line: int, end_line: int) -> ast.AST | None:
    best: ast.AST | None = None
    best_size = -1
    for node in ast.walk(tree):
        if not isinstance(node, _PY_BLOCK_TYPES):
            continue
        span = _node_span(node)
        if span is None:
            continue
        s, e = span
        if s <= start_line and e >= end_line:
            size = e - s
            if best is None or size < best_size:
                best = node
                best_size = size
    return best


def _python_best_stmt(tree: ast.AST, start_line: int, end_line: int) -> ast.AST | None:
    best: ast.AST | None = None
    best_size = -1
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt) or isinstance(node, ast.Module):
            continue
        span = _node_span(node)
        if span is None:
            continue
        s, e = span
        if s <= start_line and e >= end_line:
            size = e - s
            if best is None or size < best_size:
                best = node
                best_size = size
    return best


def _python_enclosing_defs(tree: ast.AST, start_line: int, end_line: int) -> list[ast.AST]:
    """All enclosing function/class/method defs (outermost first)."""
    out: list[tuple[ast.AST, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        span = _node_span(node)
        if span is None:
            continue
        s, e = span
        if s <= start_line and e >= end_line:
            out.append((node, e - s))
    out.sort(key=lambda t: t[1], reverse=True)  # broadest (outermost) first
    return [n for n, _ in out]


def _py_kind_label(node: ast.AST) -> str:
    return _PY_KIND_LABELS.get(type(node), "block")


def _resolve_file(repo: Path, path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_absolute():
        return p if p.is_file() else None
    cand = (repo / path).resolve()
    if cand.is_file():
        return cand
    cand2 = (repo / path.lstrip("./")).resolve()
    if cand2.is_file():
        return cand2
    return None


def _rel_path(repo: Path, file: Path) -> str:
    try:
        return str(file.relative_to(repo))
    except ValueError:
        return str(file)


def _format_scope(rel: str, defs: list[ast.AST]) -> str:
    parts: list[str] = []
    for d in defs:
        nm = getattr(d, "name", "")
        s = getattr(d, "lineno", "")
        kind = "class" if isinstance(d, ast.ClassDef) else "function"
        parts.append(f"{rel}:{s} {kind} {nm}")
    return " > ".join(parts)


def get_ast_range(
    repo_path: str | Path,
    path: str,
    start_line: int,
    end_line: int | None = None,
    max_lines: int = _MAX_AST_RANGE_LINES,
) -> dict[str, Any]:
    """Expand a line range to its enclosing AST node / block boundaries.

    Given a file and a (possibly partial) line range, returns the tightest
    enclosing semantic block: for Python this uses the real ``ast`` (smallest
    containing function/class/compound-statement, with the enclosing scope
    chain); for Go/JS/TS/Rust it uses the smallest enclosing definition found
    by the regex parser (with brace-matched body ends). This lets the agent
    understand control flow and variable scope around a grep hit without
    reading thousands of lines or guessing line ranges.

    Returns::

        {"ok": True, "path", "start_line", "end_line", "requested_start",
         "requested_end", "kind", "name", "enclosing_scope", "body",
         "truncated", "expanded"}
        {"ok": False, "error": ...}
    """
    repo = Path(repo_path).resolve()
    target = _resolve_file(repo, path)
    if target is None:
        return {"ok": False, "error": f"file not found: {path}"}
    if end_line is None:
        end_line = start_line
    start_line = max(1, int(start_line))
    end_line = max(start_line, int(end_line))
    max_lines = max(20, min(2000, int(max_lines)))
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "error": f"could not read {path}: {e}"}
    lines = source.splitlines()
    n = len(lines)
    rel = _rel_path(repo, target)
    requested = (start_line, min(end_line, n))

    bs, be = start_line, min(end_line, n)
    kind = "range"
    name = ""
    enclosing = ""

    suffix = target.suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            block = _python_best_block(tree, start_line, end_line)
            if block is None:
                block = _python_best_stmt(tree, start_line, end_line)
            if block is not None:
                span = _node_span(block)
                if span is not None:
                    bs, be = span
                    kind = _py_kind_label(block)
                    name = getattr(block, "name", "")
            defs = _python_enclosing_defs(tree, start_line, end_line)
            enclosing = _format_scope(rel, defs)
    elif suffix in SUPPORTED_SUFFIXES:
        parser = ASTParser()
        try:
            symbols = parser.extract_symbols(target)
        except Exception:
            symbols = []
        best: tuple[dict[str, Any], int, int] | None = None
        best_size = -1
        for sym in symbols:
            s = int(sym.get("line", 1))
            e = int(sym.get("end_line") or s)
            if e <= s:
                e = _find_body_end(lines, s - 1)
            if s <= start_line and e >= end_line:
                size = e - s
                if best is None or size < best_size:
                    best = (sym, s, e)
                    best_size = size
        if best is not None:
            sym, s, e = best
            bs, be = s, e
            kind = str(sym.get("kind", "symbol"))
            name = str(sym.get("name", ""))
            parent = sym.get("parent")
            if parent:
                enclosing = f"{rel}:{s} {kind} {name}"
    # Unsupported suffixes fall through to the raw clamped range.

    bs = max(1, bs)
    be = min(n, be)
    truncated = False
    if be - bs + 1 > max_lines:
        be = bs + max_lines - 1
        truncated = True
    body_lines = [f"{i:>5}: {lines[i - 1]}" for i in range(bs, be + 1) if 1 <= i <= n]
    return {
        "ok": True,
        "path": rel,
        "start_line": bs,
        "end_line": be,
        "requested_start": requested[0],
        "requested_end": requested[1],
        "kind": kind,
        "name": name,
        "enclosing_scope": enclosing,
        "body": "\n".join(body_lines),
        "truncated": truncated,
        "expanded": (bs, be) != requested,
    }
