from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import logging

from .constants import ZIRCON_DIR, zircon_path
from .context_guard import truncate_oversized_content
from .distiller import Distiller
from .types import TierConfig

logger = logging.getLogger("agent.core.context")


_PROMPT_PATH_PATTERN = re.compile(r"(?<!\w)@(?P<path>[^\s@]+)")
_MAX_PROMPT_PATH_REFERENCES = 10
_MAX_DIRECTORY_LISTING_ENTRIES = 500
_MAX_PROJECT_MEMORY_CHARS = 12000
_PROJECT_MEMORY_FILES = ("PROJECT_MEMORY.md", "AGENTS.md")


def estimate_tokens(text: str | None) -> int:
    if text is None:
        return 0
    if not text:
        return 1
    return max(1, len(text) // 4)


class LRUSet(OrderedDict):
    def __init__(self, max_size: int = 30):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.max_size:
            oldest = next(iter(self))
            del self[oldest]


@dataclass
class RepoMapEntry:
    path: str
    symbols: list[dict]
    imports: list[str]
    line_count: int


class _FilteredHistory(list):
    """A list that silently filters None items on append/extend/__setitem__."""
    def append(self, item):
        if item is not None:
            super().append(item)

    def extend(self, iterable):
        super().extend(item for item in iterable if item is not None)

    def __setitem__(self, key, value):
        if value is not None:
            super().__setitem__(key, value)
        else:
            super().__setitem__(key, {})

    def insert(self, index, item):
        if item is not None:
            super().insert(index, item)

    def __iadd__(self, other):
        self.extend(other)
        return self


class ContextManager:
    def __init__(
        self,
        repo_path: str | Path,
        context_window: int = 32000,
        safety_margin: int = 400,
        kg_memory: Any = None,
        embedder: Any = None,
        tier_config: TierConfig | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.context_window = context_window
        self.safety_margin = safety_margin
        self.max_tokens = context_window - safety_margin
        self.tier = tier_config or TierConfig(name="balanced")

        self.task = ""
        self.plan: Any | None = None
        self.current_step: Any | None = None

        self.working_set = LRUSet(max_size=self.tier.working_set_max_files)
        self.modified_files: set[str] = set()

        self.history: list[dict] = _FilteredHistory()
        self.session_notes: list[str] = []

        self.repo_map: dict[str, RepoMapEntry] = {}
        self.repo_map_text: str = ""
        self.repo_map_built = False

        self.kg = kg_memory
        self.embedder = embedder
        self.distiller = Distiller(tier_config=self.tier)

        self.episodic_memory: list[str] = []
        self._load_episodic_memory()
        self.project_memory = ""
        self.reload_project_memory()

        self._symbol_index: dict[str, list[tuple[str, int]]] = {}

        self._git_analyzer = None

    def set_task(self, task: str):
        self.task = self._append_prompt_path_context(task)

    def _append_prompt_path_context(self, task: str) -> str:
        """Append safe, bounded previews for workspace paths mentioned as @paths."""
        references = list(_PROMPT_PATH_PATTERN.finditer(task))[:_MAX_PROMPT_PATH_REFERENCES]
        if not references:
            return task

        blocks: list[str] = []
        seen: set[Path] = set()
        for match in references:
            raw_path = match.group("path").rstrip(".,;:!?)]}")
            if not raw_path:
                continue
            candidate = (self.repo_path / raw_path).resolve()
            try:
                candidate.relative_to(self.repo_path)
            except ValueError:
                continue
            if candidate in seen or not candidate.exists():
                continue
            seen.add(candidate)

            relative_path = candidate.relative_to(self.repo_path).as_posix()
            if candidate.is_file():
                try:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    logger.debug("Unable to read prompt path %s: %s", candidate, exc)
                    continue
                preview = truncate_oversized_content(content, source="prompt path", path=relative_path)
                blocks.append(f'<prompt_path_file path="{relative_path}">\n{preview}\n</prompt_path_file>')
            elif candidate.is_dir():
                try:
                    entries = sorted(
                        path.relative_to(candidate).as_posix() + ("/" if path.is_dir() else "")
                        for path in candidate.rglob("*")
                    )
                except OSError as exc:
                    logger.debug("Unable to list prompt path %s: %s", candidate, exc)
                    continue
                if len(entries) > _MAX_DIRECTORY_LISTING_ENTRIES:
                    entries = entries[:_MAX_DIRECTORY_LISTING_ENTRIES] + [
                        f"... ({len(entries) - _MAX_DIRECTORY_LISTING_ENTRIES} more entries)"
                    ]
                listing = truncate_oversized_content(
                    "\n".join(entries), source="directory listing", path=relative_path
                )
                blocks.append(f'<prompt_path_directory path="{relative_path}">\n{listing}\n</prompt_path_directory>')

        if not blocks:
            return task
        return task + "\n\n<referenced_paths>\n" + "\n\n".join(blocks) + "\n</referenced_paths>"

    def set_plan(self, plan: Any):
        self.plan = plan

    def set_current_step(self, step: Any | None):
        self.current_step = step

    def add_file_to_working_set(self, path: str, content: str | None):
        if content is None:
            return
        content = truncate_oversized_content(content, source="file content", path=path)
        max_chars = self.tier.tokens_per_file * 4
        if path in self.modified_files:
            max_chars = self.tier.modified_file_tokens * 4
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"
        self.working_set[path] = content

    def mark_modified(self, path: str):
        self.modified_files.add(path)

    def add_note(self, note: str):
        self.session_notes.append(note)
        # Cap session notes to prevent unbounded memory growth across turns
        if len(self.session_notes) > 50:
            self.session_notes = self.session_notes[-50:]

    def add_tool_exchange(self, tool_name: str, args: dict, result: str, distill: bool = True):
        # Normalize: tool results can arrive as None on failure paths; store ""
        # so downstream consumers (len(), slicing, token counting) never crash.
        result = result if isinstance(result, str) else ("" if result is None else str(result))
        if distill:
            result = self.distiller.distill_for_history(result, tool_name)
        self.history.append({"role": "assistant", "tool_call": {"name": tool_name, "arguments": args}})
        self.history.append({"role": "tool", "content": result, "tool_name": tool_name})

    def add_assistant_message(self, content: str):
        # Normalize: LLM responses and fallback paths can pass None; an
        # explicit "content": None breaks every msg.get("content", "") caller
        # (the default only fires when the key is ABSENT, not None-valued).
        content = content if isinstance(content, str) else ("" if content is None else str(content))
        # Deduplicate: chat_stream adds the final response AFTER extending
        # history with the executor's last turn, which already contains the
        # same assistant message. Don't append it twice.
        if self.history:
            last = self.history[-1]
            if last.get("role") == "assistant" and last.get("content") == content:
                return
        self.history.append({"role": "assistant", "content": content})

    def add_user_message(self, content: str):
        content = content if isinstance(content, str) else ("" if content is None else str(content))
        self.history.append({"role": "user", "content": content})

    def add_promoted_inputs(self, inputs: list[str]) -> None:
        """Add newly promoted durable inputs once, preserving their order."""
        for content in inputs:
            self.add_user_message(content)

    def clear_history(self):
        self.history.clear()


    @property
    def _archive_db_path(self) -> Path:
        return zircon_path(self.repo_path, "context_archive.db")

    def _init_archive_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._archive_db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                step_index INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_archive_task
            ON archives(task_id)
        """)
        return conn

    def archive_before_compaction(self, old_messages: list[dict], step_index: int = 0) -> None:
        if not old_messages:
            return
        try:
            conn = self._init_archive_db()
            try:
                conn.execute(
                    "INSERT INTO archives (task_id, data, timestamp, step_index) VALUES (?, ?, ?, ?)",
                    (
                        self.task[:200] if self.task else "unknown",
                        json.dumps(old_messages, default=str, ensure_ascii=False),
                        datetime.utcnow().isoformat(),
                        step_index,
                    ),
                )
                conn.execute(
                    "DELETE FROM archives WHERE id NOT IN (SELECT id FROM archives ORDER BY id DESC LIMIT 50)"
                )
                conn.commit()
            finally:
                conn.close()
            logger.debug("Archived %d messages before compaction", len(old_messages))
        except Exception as e:
            logger.debug("Failed to archive context: %s", e)

    def retrieve_archived_context(self, max_lines: int = 20) -> str:
        try:
            conn = self._init_archive_db()
            try:
                rows = conn.execute(
                    "SELECT data, timestamp FROM archives ORDER BY id DESC LIMIT 1"
                ).fetchall()
            finally:
                conn.close()
            if not rows:
                return ""
            raw = json.loads(rows[0][0])
            lines = []
            for msg in raw[-max_lines:]:
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))[:300]
                if content:
                    lines.append(f"[{role}] {content}")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("Failed to retrieve archived context: %s", e)
            return ""

    async def compact_history(self, router) -> None:
        if self.tier.history_compact_threshold > 90000:
            return  # disabled (e.g., low tier uses truncation)
        total = sum(
            estimate_tokens(content) if isinstance(content := msg.get("content"), str) else 0
            for msg in self.history
            if isinstance(msg, dict)
        )
        if total < self.tier.history_compact_threshold:
            return

        keep = self.tier.history_keep_exchanges * 2
        if len(self.history) <= keep:
            return

        to_summarize = self.history[:-keep]
        recent = self.history[-keep:]

        self.archive_before_compaction(to_summarize)

        from ..llm.prompts import SYSTEM_HISTORY_SUMMARIZER
        blocks = []
        for msg in to_summarize:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            content = msg.get("content")
            if not isinstance(content, str):
                content = ""
            if len(content) > 1000:
                content = content[:1000] + "..."
            blocks.append(f"[{role}] {content}")
        history_block = "\n".join(blocks)

        messages = [
            {"role": "system", "content": SYSTEM_HISTORY_SUMMARIZER},
            {"role": "user", "content": f"Conversation history:\n\n{history_block}"},
        ]
        try:
            response = await router.generate(role="summarize", messages=messages, max_tokens=512)
            summary = response.content
        except Exception:
            summary = "[History summarized due to length]"

        # Strip trailing tool messages from recent history that would be orphaned
        # (a tool message must have a preceding assistant message with tool_calls)
        cleaned_recent = list(recent)
        while cleaned_recent and cleaned_recent[-1].get("role") == "tool":
            cleaned_recent.pop()
        # Also strip standalone tool messages at the start of recent
        while cleaned_recent and cleaned_recent[0].get("role") == "tool":
            cleaned_recent.pop(0)

        self.history = [
            {"role": "user", "content": "<history_summary>Conversation summary of earlier messages</history_summary>"},
            {"role": "assistant", "content": f"<history_summary>\n{summary}\n</history_summary>"},
        ] + cleaned_recent

    SKIP_DIRS = {
        ".git", ".svn", "__pycache__", "node_modules", "venv", ".venv",
        ".env", "env", "dist", "build", ".tox", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".hypothesis",
        "target",           # Rust build dir
        "bin", "obj",       # .NET build dirs
        "vendor",           # Go / PHP vendor
        ".dart_tool", "coverage",
        "polyglot-benchmark",
        "benchmark",
    }
    MAX_REPO_MAP_FILES = 3000  # cap to prevent 60s+ scans
    MAX_AUTO_INDEX_FILE_BYTES = 40 * 1024 * 1024

    SOURCE_EXTENSIONS = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
        ".sqf", ".cpp", ".hpp", ".c", ".h", ".cs", ".java", ".kt",
        ".go", ".rs", ".rb", ".php", ".swift", ".lua",
        ".sh", ".bash", ".zsh", ".ps1",
        ".sql", ".r", ".R", ".scala", ".clj", ".ex", ".exs",
        ".vim", ".el", ".lisp",
    }

    REPO_MAP_CACHE_FILE = "repo_map_cache.json"

    def _repo_map_cache_path(self) -> Path:
        return self.repo_path / ZIRCON_DIR / self.REPO_MAP_CACHE_FILE

    def _load_repo_map_from_cache(self) -> bool:
        cache_path = self._repo_map_cache_path()
        if not cache_path.exists():
            return False
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            self.repo_map.clear()
            for k, v in data.get("map", {}).items():
                self.repo_map[k] = RepoMapEntry(
                    path=v.get("path", k),
                    symbols=v.get("symbols", []),
                    imports=v.get("imports", []),
                    line_count=v.get("line_count", 0),
                )
            self._symbol_index.clear()
            for sym_key, val in data.get("symbol_index", {}).items():
                self._symbol_index[sym_key] = [(item[0], item[1]) for item in val]
            self.repo_map_text = data.get("map_text", "")
            self.repo_map_built = True
            logger.debug("Loaded repo map from cache: %d files", len(self.repo_map))
            return True
        except Exception as e:
            logger.debug("Failed to load repo map cache: %s", e)
            self.repo_map.clear()
            self._symbol_index.clear()
            self.repo_map_built = False
            return False

    def _save_repo_map_to_cache(self):
        cache_path = self._repo_map_cache_path()
        try:
            data = {
                "map": {
                    k: {
                        "path": v.path,
                        "symbols": v.symbols,
                        "imports": v.imports,
                        "line_count": v.line_count,
                    }
                    for k, v in self.repo_map.items()
                },
                "symbol_index": {k: v for k, v in self._symbol_index.items()},
                "map_text": self.repo_map_text,
            }
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data, separators=(",", ":"), default=str), encoding="utf-8")
            logger.debug("Saved repo map to cache: %d files, %s", len(self.repo_map), cache_path)
        except Exception as e:
            logger.debug("Failed to save repo map cache: %s", e)

    def build_repo_map(self, progress_callback=None):
        """Build repo map synchronously (blocking). Kept for backward compat.
        
        For non-blocking async rebuild see build_repo_map_async().
        """
        if self._load_repo_map_from_cache():
            return  # skip full scan

        self._build_repo_map_internal(progress_callback=progress_callback)

    def _iter_source_files(self):
        """Yield (abs_path, rel_path) for source files, pruning skipped dirs in place.

        Uses os.walk with in-place dirnames mutation so skipped dirs (.git,
        node_modules, venv, etc.) are never descended into, and only kept
        source files are sorted — far cheaper than rglob("*") + post-filter.
        """
        repo = self.repo_path
        for root, dirnames, filenames in os.walk(repo):
            # Prune skipped dirs in place so os.walk does not descend into them
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in self.SKIP_DIRS
            ]
            dirnames.sort()
            for fname in sorted(filenames):
                suffix = Path(fname).suffix.lower()
                if suffix not in self.SOURCE_EXTENSIONS:
                    continue
                full = Path(root) / fname
                try:
                    if full.stat().st_size > self.MAX_AUTO_INDEX_FILE_BYTES:
                        logger.debug("Skipping oversized file during automatic indexing: %s", full)
                        continue
                except OSError:
                    continue
                try:
                    rel = str(full.relative_to(repo)).replace("\\", "/")
                except ValueError:
                    continue
                yield full, rel

    def _build_repo_map_internal(self, progress_callback=None, after_cache_save=None):
        """Core repo map building logic, shared by sync and async paths."""
        self.repo_map.clear()
        self._symbol_index.clear()
        scanned = 0
        for source_file, rel in self._iter_source_files():
            scanned += 1
            if scanned > self.MAX_REPO_MAP_FILES:
                break

            if progress_callback and scanned % 50 == 0:
                progress_callback(scanned)

            try:
                source = source_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            suffix = source_file.suffix.lower()
            if suffix == ".py":
                symbols, imports = self._parse_python_symbols(source, rel)
            else:
                symbols, imports = self._parse_generic_symbols(source, rel, suffix)

            self.repo_map[rel] = RepoMapEntry(
                path=rel, symbols=symbols, imports=imports, line_count=len(source.splitlines()),
            )
            if self.kg:
                self.kg.ingest_file_structure(rel, symbols)
                for imp in imports:
                    self.kg.ingest_import(rel, imp.replace(".", "/") + suffix)

        self._rank_repo_map()
        self.repo_map_built = True
        self._save_repo_map_to_cache()
        if after_cache_save:
            after_cache_save()

    async def build_repo_map_async(self, progress_callback=None):
        """Non-blocking async variant. Loads cache immediately if valid.
        
        On cache miss the full build runs in a thread executor so it does
        not block the event loop.
        """
        # Always try cache first — fast path
        if self._load_repo_map_from_cache():
            return

        # No cache at all: run the full build off the event loop
        import asyncio

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._build_repo_map_internal(progress_callback=progress_callback),
        )

    def get_repo_map_text_with_progress(self) -> str:
        """Return repo map text with a progress annotation if not fully built."""
        text = self.repo_map_text or ""
        total = len(self.repo_map)
        if total > 0:
            # If cache was loaded, we know we have full data
            if self.repo_map_built:
                return text
            progress = f"<!-- repo-map: {total} files indexed -->"
            return f"{progress}\n{text}" if text else progress
        return text or ""

    def _parse_python_symbols(self, source: str, rel: str) -> tuple[list[dict], list[str]]:
        symbols = []
        imports = []
        try:
            tree = ast.parse(source)
        except (SyntaxError, Exception):
            return symbols, imports

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    imports.append(alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sym = {"name": node.name, "kind": "function", "line": node.lineno, "parent": None}
                symbols.append(sym)
                self._symbol_index.setdefault(node.name.lower(), []).append((rel, node.lineno))
            elif isinstance(node, ast.ClassDef):
                sym = {"name": node.name, "kind": "class", "line": node.lineno, "parent": None}
                symbols.append(sym)
                self._symbol_index.setdefault(node.name.lower(), []).append((rel, node.lineno))
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        msym = {"name": f"{node.name}.{item.name}", "kind": "method", "line": item.lineno, "parent": node.name}
                        symbols.append(msym)
                        self._symbol_index.setdefault(item.name.lower(), []).append((rel, item.lineno))

        return symbols, imports

    def _parse_generic_symbols(self, source: str, rel: str, suffix: str) -> tuple[list[dict], list[str]]:
        import re
        symbols = []
        imports = []

        if suffix == ".sqf":
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                m = re.match(r'(\w+_fnc_\w+)\s*=\s*\{', stripped)
                if not m:
                    m = re.match(r'(\w+)\s*=\s*\{', stripped)
                if m:
                    name = m.group(1)
                    sym = {"name": name, "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(name.lower(), []).append((rel, i))
                if stripped.startswith("#include"):
                    imp = stripped.replace("#include", "").strip().strip('"<>')
                    imports.append(imp)

        elif suffix in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                m = re.match(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)', stripped)
                if m:
                    sym = {"name": m.group(1), "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                    continue
                m = re.match(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', stripped)
                if m:
                    sym = {"name": m.group(1), "kind": "class", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                    continue
                m = re.match(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(', stripped)
                if m:
                    sym = {"name": m.group(1), "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                if stripped.startswith("import "):
                    imports.append(stripped)

        elif suffix in (".c", ".cpp", ".hpp", ".h", ".cs", ".java", ".kt"):
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                m = re.match(r'(?:public|private|protected|static|virtual|override|inline|extern)\s+.*?\s+(\w+)\s*\([^)]*\)\s*(?:\{|$)', stripped)
                if m and not any(kw in stripped for kw in ("if", "while", "for", "switch", "catch", "return")):
                    sym = {"name": m.group(1), "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                    continue
                m = re.match(r'(?:public\s+)?(?:abstract\s+)?(?:class|struct|enum|interface)\s+(\w+)', stripped)
                if m:
                    kind = "class" if "class" in stripped else "struct"
                    sym = {"name": m.group(1), "kind": kind, "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                if stripped.startswith("#include") or stripped.startswith("using ") or stripped.startswith("import "):
                    imports.append(stripped)

        elif suffix == ".go":
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                m = re.match(r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)', stripped)
                if m:
                    sym = {"name": m.group(1), "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                if stripped.startswith("import"):
                    imports.append(stripped)

        elif suffix == ".rs":
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                m = re.match(r'pub\s+(?:async\s+)?fn\s+(\w+)', stripped)
                if not m:
                    m = re.match(r'fn\s+(\w+)', stripped)
                if m:
                    sym = {"name": m.group(1), "kind": "function", "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                    continue
                m = re.match(r'pub\s+(?:struct|enum|trait|impl)\s+(\w+)', stripped)
                if not m:
                    m = re.match(r'(?:struct|enum|trait|impl)\s+(\w+)', stripped)
                if m:
                    kind = "class" if "struct" in stripped else "class"
                    sym = {"name": m.group(1), "kind": kind, "line": i, "parent": None}
                    symbols.append(sym)
                    self._symbol_index.setdefault(m.group(1).lower(), []).append((rel, i))
                if stripped.startswith("use "):
                    imports.append(stripped)

        else:
            pass

        return symbols, imports

    def _rank_repo_map(self):
        if not self.kg:
            self.repo_map_text = self._format_repo_map_text()
            return

        # Precompute each file's joined-imports string exactly once, so we
        # never re-run " ".join(e.imports) inside the scoring loop.
        file_import_joined = {
            p: " ".join(e.imports) for p, e in self.repo_map.items()
        }
        paths = list(self.repo_map.keys())

        # Build per-distinct-import-token counts once:
        #   C[token] = # files whose imports contain token
        #   M[token] = # files whose path contains token
        # (Both use substring matching, matching the original semantics.)
        token_counts: dict[str, tuple[int, int]] = {}
        for entry in self.repo_map.values():
            for imp in entry.imports:
                imp_path = imp.replace(".", "/")
                if not imp_path or imp_path in token_counts:
                    continue
                c = sum(1 for joined in file_import_joined.values() if imp_path in joined)
                m = sum(1 for p in paths if imp_path in p)
                token_counts[imp_path] = (c, m)

        # Score each file in a single pass over its own imports.
        import_scores: dict[str, float] = {}
        for path, entry in self.repo_map.items():
            score = 1.0
            if path in self.modified_files:
                score += 5.0
            for imp in entry.imports:
                c, m = token_counts.get(imp.replace(".", "/"), (1, 0))
                if m:
                    score += 0.5 / max(1, c) * m
            import_scores[path] = score

        ranked = sorted(self.repo_map.items(), key=lambda x: import_scores.get(x[0], 1.0), reverse=True)
        self.repo_map = dict(ranked)
        self.repo_map_text = self._format_repo_map_text()

    def _format_repo_map_text(self) -> str:
        lines = []
        detail = self.tier.repo_map_detail
        max_files = self.tier.repo_map_max_files
        for i, (path, entry) in enumerate(self.repo_map.items()):
            if i >= max_files:
                lines.append(f"  ... ({len(self.repo_map) - max_files} more files)")
                break
            if detail == "minimal":
                names = ", ".join(sym["name"] for sym in entry.symbols[:8])
                if names:
                    lines.append(f"{path}: {names}")
                else:
                    lines.append(path)
            elif detail == "full":
                sym_strs = []
                for sym in entry.symbols:
                    indent = "  " if sym.get("parent") else ""
                    sym_strs.append(f"{sym['line']}:{indent}{sym['kind']} {sym['name']}")
                doc_lines = []
                if entry.symbols:
                    first_sym = entry.symbols[0]
                    doc_lines.append(f"  # {len(entry.symbols)} symbols")
                if sym_strs:
                    lines.append(f"{path} ({entry.line_count}L):\n  " + "\n  ".join(sym_strs))
                else:
                    lines.append(f"{path} ({entry.line_count}L)")
            else:  # standard
                sym_strs = []
                for sym in entry.symbols:
                    indent = "  " if sym.get("parent") else ""
                    sym_strs.append(f"{sym['line']}:{indent}{sym['kind']} {sym['name']}")
                if sym_strs:
                    lines.append(f"{path} ({entry.line_count}L):\n  " + "\n  ".join(sym_strs))
                else:
                    lines.append(f"{path} ({entry.line_count}L)")
        return "\n".join(lines)

    def semantic_search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        if not self.embedder or not self.repo_map or not self.tier.semantic_search_enabled:
            return []
        candidates = {}
        for path, entry in self.repo_map.items():
            parts = [f"{sym['name']} {sym['kind']}" for sym in entry.symbols]
            candidates[path] = f"{path} " + " ".join(parts)
        return self.embedder.top_k(query, candidates, k)

    def find_symbol(self, name: str) -> list[tuple[str, int]]:
        return self._symbol_index.get(name.lower(), [])

    def build_messages(
        self,
        system_prompt: str,
        tools_description: str = "",
    ) -> list[dict]:
        messages = []
        budget = self.max_tokens

        system = system_prompt.format(tool_descriptions=tools_description) if tools_description else system_prompt
        messages.append({"role": "system", "content": system})
        budget -= estimate_tokens(system)

        if self.project_memory:
            memory_budget = min(max(budget // 8, 0), _MAX_PROJECT_MEMORY_CHARS // 4)
            memory_text = self.project_memory[: memory_budget * 4]
            if memory_text and estimate_tokens(memory_text) <= budget:
                messages.append({
                    "role": "system",
                    "content": f"<project_memory>\n{memory_text}\n</project_memory>",
                })
                budget -= estimate_tokens(memory_text)

        if self.repo_map_text:
            map_budget = min(budget // 4, 2000)
            map_text = self.repo_map_text
            if estimate_tokens(map_text) > map_budget:
                lines = map_text.splitlines()
                included = []
                for line in lines:
                    if estimate_tokens("\n".join(included) + line) > map_budget:
                        break
                    included.append(line)
                map_text = "\n".join(included)
            messages.append({"role": "system", "content": f"<repo_map>\n{map_text}\n</repo_map>"})
            budget -= estimate_tokens(map_text)

        working_ctx = self._build_working_set_context(budget)
        if working_ctx:
            messages.append({"role": "system", "content": working_ctx})
            budget -= estimate_tokens(working_ctx)

        if self.task:
            task_msg = f"<task>\n{self.task}\n</task>"
            messages.append({"role": "user", "content": task_msg})
            budget -= estimate_tokens(task_msg)

        if self.tier.name == "quality":
            git_ctx = self._get_git_conventions_context()
            if git_ctx and estimate_tokens(git_ctx) < budget // 6:
                messages.append({"role": "system", "content": git_ctx})
                budget -= estimate_tokens(git_ctx)

        if self.plan:
            plan_msg = f"<plan>\n{self._format_plan()}\n</plan>"
            messages.append({"role": "system", "content": plan_msg})
            budget -= estimate_tokens(plan_msg)

        kg_ctx = self._get_kg_context()
        if kg_ctx and estimate_tokens(kg_ctx) < budget // 4:
            messages.append({"role": "system", "content": f"<related>\n{kg_ctx}\n</related>"})
            budget -= estimate_tokens(kg_ctx)

        if self.tier.episodic_memory_count > 0 and self.episodic_memory:
            em_items = self.episodic_memory[-self.tier.episodic_memory_count:]
            em_text = "<learnings>\n" + "\n".join(f"- {n}" for n in em_items) + "\n</learnings>"
            if estimate_tokens(em_text) < budget // 6:
                messages.append({"role": "system", "content": em_text})
                budget -= estimate_tokens(em_text)

        if self.session_notes:
            notes_text = "<notes>\n" + "\n".join(f"- {n}" for n in self.session_notes[-10:]) + "\n</notes>"
            if estimate_tokens(notes_text) < budget // 8:
                messages.append({"role": "system", "content": notes_text})
                budget -= estimate_tokens(notes_text)

        progress_text = self._build_progress_note()
        if progress_text and estimate_tokens(progress_text) < budget // 8:
            messages.append({"role": "system", "content": progress_text})
            budget -= estimate_tokens(progress_text)

        history_msgs = self._get_recent_history(budget)
        messages.extend(history_msgs)

        return [
            {
                **message,
                "content": truncate_oversized_content(
                    content,
                    source="tool result" if message.get("role") == "tool" else f"{message.get('role', 'message')} message",
                ),
            }
            if isinstance(content := message.get("content"), str)
            else message
            for message in messages
        ]

    def _build_working_set_context(self, budget: int) -> str:
        parts = []
        remaining = budget
        priority_paths = list(self.modified_files) + [p for p in self.working_set if p not in self.modified_files]

        for path in priority_paths:
            content = self.working_set.get(path)
            if content is None:
                continue
            entry = f'<file path="{path}">\n{content}\n</file>'
            tokens = estimate_tokens(entry)
            if tokens > remaining:
                truncated = content[:remaining * 4]
                entry = f'<file path="{path}" truncated>\n{truncated}\n</file>'
                tokens = estimate_tokens(entry)
                if tokens > remaining:
                    break
            parts.append(entry)
            remaining -= tokens

        return "\n".join(parts) if parts else ""

    def _format_plan(self) -> str:
        if not self.plan:
            return ""
        lines = []
        for step in self.plan.steps:
            marker = ">> " if self.current_step and step.index == self.current_step.index else "   "
            lines.append(f"{marker}{step.index}. [{step.action}] {step.description}")
            if step.target_files:
                lines.append(f"      files: {', '.join(step.target_files)}")
        return "\n".join(lines)

    def _get_kg_context(self) -> str:
        if not self.kg or not self.task or self.tier.kg_context_nodes <= 0:
            return ""
        return self.kg.get_context_for_task(self.task, max_nodes=self.tier.kg_context_nodes)

    def _get_recent_history(self, budget: int) -> list[dict]:
        if not self.history:
            return []
        result = []
        remaining = budget
        keep = self.tier.history_keep_exchanges * 2
        recent = self.history[-keep:] if len(self.history) > keep else self.history

        for idx, msg in enumerate(reversed(recent)):
            content = msg.get("content", "")
            tokens = estimate_tokens(content)

            if idx < 2:
                if msg.get("role") == "tool":
                    result.insert(0, {"role": "tool", "content": msg.get("content", ""), "tool_call_id": msg.get("tool_call_id", "")})
                else:
                    result.insert(0, msg)
                remaining -= tokens
                continue

            if tokens > remaining:
                break
            if msg.get("role") == "tool":
                result.insert(0, {"role": "tool", "content": msg.get("content", ""), "tool_call_id": msg.get("tool_call_id", "")})
            else:
                result.insert(0, msg)
            remaining -= tokens

        # Never let the window start with orphaned tool results: if the
        # assistant message carrying their tool_calls fell outside the
        # budget (common with parallel tool-call batches), providers reject
        # the whole request (400) and the tool loop dies mid-task.
        while result and result[0].get("role") == "tool":
            result.pop(0)
        return result

    @property
    def files_modified_list(self) -> str:
        return "\n".join(sorted(self.modified_files)) if self.modified_files else "No files modified"

    def working_set_summary(self) -> str:
        if not self.working_set:
            return "No files in working set."
        return "Working set:\n" + "\n".join(f"  {p}" for p in self.working_set)

    def _get_git_conventions_context(self) -> str:
        if self._git_analyzer is None:
            from .git_context import GitConventionAnalyzer
            self._git_analyzer = GitConventionAnalyzer(str(self.repo_path))
        if not self._git_analyzer.is_available():
            return ""
        return self._git_analyzer.format_context(self.task)

    def _build_progress_note(self) -> str:
        lines = ["<progress>"]
        if self.modified_files:
            lines.append(f"Files already modified this session: {', '.join(sorted(self.modified_files))}")
        else:
            lines.append("No files modified yet this session.")
        if self.working_set:
            recent_reads = [p for p in self.working_set.keys() if p not in self.modified_files][-8:]
            if recent_reads:
                lines.append(f"Recently read (no need to re-read unless changed): {', '.join(recent_reads)}")
        lines.append("Reminder: do not repeat the same tool call. If stuck, summarize progress and pick a new action.")
        lines.append("</progress>")
        return "\n".join(lines)

    def save_episodic_memory(self, learning: str):
        self.episodic_memory.append(learning)
        self._save_episodic_memory()

    def _load_episodic_memory(self):
        path = self.repo_path / ZIRCON_DIR / "learnings.json"
        if path.exists():
            try:
                self.episodic_memory = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.episodic_memory = []

    def _save_episodic_memory(self):
        path = self.repo_path / ZIRCON_DIR / "learnings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.episodic_memory[-50:], indent=2, ensure_ascii=False), encoding="utf-8")

    def reload_project_memory(self) -> None:
        """Load bounded, human-maintained project guidance for every session."""
        sections: list[str] = []
        remaining = _MAX_PROJECT_MEMORY_CHARS
        for name in _PROJECT_MEMORY_FILES:
            path = self.repo_path / name
            if not path.is_file() or remaining <= 0:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as exc:
                logger.debug("Unable to load project memory %s: %s", path, exc)
                continue
            if not content:
                continue
            section = f"## {name}\n{content}"
            sections.append(section[:remaining])
            remaining -= len(sections[-1])
        self.project_memory = "\n\n".join(sections)

    def distill_observation(self, data: str, focus: str = "") -> str:
        if focus:
            return self.distiller.mask_observation(data, focus)
        return self.distiller.distill(data)
