"""Hierarchical Fault Localization (FL).

A strict, multi-phase, top-down localization pipeline that runs BEFORE the
active repair agent loop. It replaces free-form grep/file-navigation exploration
with a funnel that progressively narrows the search space:

    Repository ──> Phase 1: File-level IR (BM25 + embeddings) ──> Targets
                     └──> Phase 2: Structural parse (classes & functions) ──> Suspects
                            └──> Phase 3: Line-level edit window ──> Precise context

By the time the main repair agent receives its prompt, it is looking at a
surgical context snippet containing only the ~50 lines surrounding the bug,
not the whole codebase. This keeps the repair agent's token budget focused on
the fix rather than on locating the bug.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..llm.structured import extract_json
from ..llm.prompts import SYSTEM_FAULT_SUSPECT_CLASSIFIER, SYSTEM_FAULT_LINE_PINPOINT
from ..parsers.ast_parser import ASTParser
from .exclusions import EXCLUDED_DIRS, is_excluded
from .types import (
    FileTarget,
    FunctionSuspect,
    LineWindow,
    LocalizationResult,
)

logger = logging.getLogger("agent.core.fault_localizer")


# Directory and extension filters kept in sync with ContextManager so the IR
# phase scans exactly the same source set the repo map would.
_SKIP_DIRS = {
    ".git", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    ".env", "env", "dist", "build", ".tox", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".hypothesis",
    "target", "bin", "obj", "vendor", ".dart_tool", "coverage",
    "polyglot-benchmark", "benchmark",
} | set(EXCLUDED_DIRS)
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".sqf", ".cpp", ".hpp", ".c", ".h", ".cs", ".java", ".kt",
    ".go", ".rs", ".rb", ".php", ".swift", ".lua",
    ".sh", ".bash", ".zsh", ".ps1",
    ".sql", ".r", ".R", ".scala", ".clj", ".ex", ".exs",
    ".vim", ".el", ".lisp",
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "if",
    "in", "into", "is", "it", "no", "not", "of", "on", "or", "such", "that",
    "the", "their", "then", "there", "these", "they", "this", "to", "was",
    "will", "with", "we", "you", "i", "do", "does", "did", "has", "have",
    "had", "been", "from", "when", "where", "which", "who", "whom", "what",
    "why", "how", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "than", "too", "very", "can", "will", "just", "should",
    "now", "out", "up", "down", "over", "under", "again", "further", "once",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])([A-Z])")
_RRF_K = 60

# Best-effort generic signature regex for non-Python files.
_GENERIC_SIG_RE = re.compile(
    r"""^\s*(?:
        (?P<py>def\s+(?P<pyn>\w+))                           |
        (?P<js>(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<jsn>\w+)) |
        (?P<arrow>(?:export\s+)?(?:const|let|var)\s+(?P<an>\w+)\s*=\s*(?:async\s*)?\(?.*?\)?\s*=>) |
        (?P<go>func\s+(?:\([^)]*\)\s+)?(?P<gon>\w+))          |
        (?P<rs>(?:pub\s+)?fn\s+(?P<rsn>\w+))                  |
        (?P<rb>def\s+(?P<rbn>\w+))                            |
        (?P<php>function\s+(?P<phpn>\w+))                     |
        (?P<swift>func\s+(?P<swiftn>\w+))                     |
        (?P<lua>function\s+(?P<luan>\w+))                     |
        (?P<cs>(?:public|private|protected|static|async|\s)+(?:\w+\s+)?(?P<csn>\w+)\s*\([^;]*\)\s*(?:\{|;)) |
        (?P<cls>(?:export\s+)?(?:default\s+)?class\s+(?P<clsn>\w+)) |
        (?P<struct>struct\s+(?P<structn>\w+))                 |
        (?P<iface>interface\s+(?P<ifacen>\w+))
    )""", re.VERBOSE,
)


def _split_identifier(name: str) -> list[str]:
    """Split a path/symbol identifier into IR tokens.

    Handles '/', '.', '_', '-', and camelCase boundaries.
    """
    cleaned = _CAMEL_BOUNDARY.sub(r" \1", name.replace("/", " ").replace(".", " ")
                                  .replace("_", " ").replace("-", " "))
    return [t for t in cleaned.lower().split() if t]


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9_]+", text.lower())
    return [t for t in raw if len(t) >= 2 and t not in _STOPWORDS]


@dataclass
class _Doc:
    path: str
    tokens: list[str]
    text: str  # representative text for embedding


class BM25:
    """Okapi BM25 over a fixed corpus of tokenized documents."""

    def __init__(self, docs: list[_Doc], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.n = len(docs)
        self.doc_len = [len(d.tokens) for d in docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0

        # document frequency per term
        self.df: dict[str, int] = {}
        # term frequency per document
        self.tf: list[dict[str, int]] = []
        for d in docs:
            freqs: dict[str, int] = {}
            for tok in d.tokens:
                freqs[tok] = freqs.get(tok, 0) + 1
            self.tf.append(freqs)
            for term in freqs:
                self.df[term] = self.df.get(term, 0) + 1

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        # +1 smoothing form of Okapi IDF (always non-negative)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query_terms: list[str]) -> list[float]:
        if self.n == 0:
            return []
        unique_q = list(dict.fromkeys(query_terms))
        scores = [0.0] * self.n
        for i in range(self.n):
            dl = self.doc_len[i] or 1
            denom_norm = self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
            tf_i = self.tf[i]
            s = 0.0
            for term in unique_q:
                tf = tf_i.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf(term)
                if idf <= 0:
                    continue
                s += idf * (tf * (self.k1 + 1)) / (tf + denom_norm)
            scores[i] = s
        return scores


class FaultLocalizer:
    """Runs the three-phase hierarchical fault-localization pipeline."""

    def __init__(
        self,
        repo_path: str | Path,
        router: Any | None = None,
        embedder: Any | None = None,
        ast_parser: ASTParser | None = None,
        top_k_files: int = 5,
        top_suspects: int = 3,
        snippet_lines: int = 50,
        max_file_chars_for_ir: int = 6000,
        max_files_for_ir: int = 3000,
        total_timeout_seconds: float = 20.0,
        llm_timeout_seconds: float = 8.0,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.router = router
        self.embedder = embedder
        self.ast = ast_parser or ASTParser()
        self.top_k_files = max(1, top_k_files)
        self.top_suspects = max(1, top_suspects)
        self.snippet_lines = max(10, snippet_lines)
        self.max_file_chars_for_ir = max_file_chars_for_ir
        self.max_files_for_ir = max_files_for_ir
        # Localization is an optimization, never a reason to block a repair.
        # These caps are deliberately independent of the router's retry/failover
        # policy, which can otherwise turn two cheap LLM calls into minutes.
        self.total_timeout_seconds = max(1.0, total_timeout_seconds)
        self.llm_timeout_seconds = max(0.5, llm_timeout_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def localize(
        self,
        bug_report: str,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> LocalizationResult:
        result = LocalizationResult(bug_report=bug_report)

        def _emit(phase: str, detail: str) -> None:
            logger.info("FL %s: %s", phase, detail)
            if progress_callback:
                try:
                    progress_callback(phase, detail)
                except Exception:
                    pass

        try:
            async with asyncio.timeout(self.total_timeout_seconds):
                # --- Phase 1: file-level IR ---
                _emit("phase1", "file-level IR (BM25 + embeddings)")
                targets = self._phase_file_level(bug_report)
                result.targets = targets
                result.used_embeddings = self.embedder is not None
                if not targets:
                    _emit("phase1", "no candidate files found")
                    return result
                _emit("phase1", f"top {len(targets)} files: " + ", ".join(t.path for t in targets))

                if self.router is None:
                    self._apply_fallback_result(result, targets)
                    return result

                # --- Phase 2: structural parse + cheap-LLM suspect classification ---
                _emit("phase2", "structural parse (classes & functions)")
                suspects = await self._phase_structural(bug_report, targets, _emit)
                result.suspects = suspects
                if not suspects:
                    _emit("phase2", "no suspects identified")
                    return result
                _emit("phase2", f"{len(suspects)} suspect(s): " + ", ".join(s.symbol for s in suspects))

                # --- Phase 3: line-level edit window ---
                _emit("phase3", "line-level edit window")
                windows = await self._phase_line_window(bug_report, suspects, _emit)
                result.windows = windows
                result.primary_window = self._pick_primary(windows)
                if result.primary_window:
                    result.snippet = self._build_snippet(result.primary_window)
                    _emit("phase3", f"primary window: {result.primary_window.file}:"
                          f"{result.primary_window.start_line}-{result.primary_window.end_line}")
                else:
                    _emit("phase3", "no precise window produced")
        except TimeoutError:
            _emit("timeout", "localization deadline reached; using available fallback")
            if result.targets and not result.primary_window:
                self._apply_fallback_result(result, result.targets)
        return result

    def _apply_fallback_result(self, result: LocalizationResult, targets: list[FileTarget]) -> None:
        """Populate a usable whole-file fallback without another LLM request."""
        if not result.suspects:
            result.suspects = self._fallback_suspects(targets)
        if not result.windows:
            result.windows = self._fallback_windows(result.suspects)
        result.primary_window = self._pick_primary(result.windows)
        if result.primary_window:
            result.snippet = self._build_snippet(result.primary_window)

    def format_context_block(self, result: LocalizationResult) -> str:
        """Render a LocalizationResult as an injectable context block."""
        if not result.ok:
            return ""
        lines = ["<fault_localization>"]
        lines.append(f"Bug report: {result.bug_report[:500]}")
        if result.targets:
            lines.append("Candidate files (Phase 1, file-level IR):")
            for t in result.targets:
                lines.append(f"  - {t.path} (score={t.score:.3f}, via {t.source})")
        if result.suspects:
            lines.append("Suspect functions (Phase 2, structural):")
            for s in result.suspects:
                lines.append(f"  - {s.file}:{s.line} {s.kind} {s.symbol}"
                             f" (conf={s.confidence:.2f}) — {s.reason}")
        if result.primary_window:
            w = result.primary_window
            lines.append(f"Primary edit window (Phase 3): {w.file}:{w.start_line}-{w.end_line}"
                         f" (conf={w.confidence:.2f}) — {w.rationale}")
        if result.snippet:
            lines.append("")
            lines.append("Surgical context snippet (the ONLY code region the repair"
                         " agent needs to focus on):")
            lines.append("```")
            lines.append(result.snippet)
            lines.append("```")
        lines.append("</fault_localization>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Phase 1: file-level IR
    # ------------------------------------------------------------------

    def _iter_source_files(self) -> list[tuple[Path, str]]:
        out: list[tuple[Path, str]] = []
        for root, dirnames, filenames in os.walk(self.repo_path):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
            dirnames.sort()
            for fname in sorted(filenames):
                if Path(fname).suffix.lower() not in _SOURCE_EXTENSIONS:
                    continue
                full = Path(root) / fname
                try:
                    rel = str(full.relative_to(self.repo_path)).replace("\\", "/")
                except ValueError:
                    continue
                if is_excluded(rel):
                    continue
                out.append((full, rel))
                if len(out) >= self.max_files_for_ir:
                    return out
        return out

    def _phase_file_level(self, bug_report: str) -> list[FileTarget]:
        files = self._iter_source_files()
        if not files:
            return []

        docs: list[_Doc] = []
        path_by_idx: list[str] = []
        for full, rel in files:
            try:
                with full.open("r", encoding="utf-8", errors="replace") as handle:
                    source = handle.read(self.max_file_chars_for_ir)
            except Exception:
                continue
            symbols = self._symbols_for_text(source, full, rel)
            sym_text = " ".join(s.get("name", "") for s in symbols) if symbols else ""
            # Representative blob: path tokens + symbol names + head of source.
            head = source[: self.max_file_chars_for_ir]
            text = f"{rel}\n{sym_text}\n{head}"
            tokens = _tokenize(text) + _split_identifier(rel)
            docs.append(_Doc(path=rel, tokens=tokens, text=text))
            path_by_idx.append(rel)

        if not docs:
            return []

        query_terms = _tokenize(bug_report)
        bm25 = BM25(docs)
        bm25_scores = bm25.score(query_terms) if query_terms else [0.0] * len(docs)

        # Rank by BM25 (descending)
        bm25_order = sorted(range(len(docs)), key=lambda i: bm25_scores[i], reverse=True)

        embed_scores: list[float] | None = None
        if self.embedder is not None and query_terms:
            try:
                candidates = {d.path: d.text[:2000] for d in docs}
                embed_hits = self.embedder.top_k(bug_report, candidates, k=len(docs))
                embed_scores = [0.0] * len(docs)
                score_map = {p: s for p, s in embed_hits}
                for i, d in enumerate(docs):
                    embed_scores[i] = score_map.get(d.path, 0.0)
            except Exception as e:
                logger.debug("embedding phase skipped: %s", e)
                embed_scores = None

        if embed_scores is not None:
            embed_order = sorted(range(len(docs)), key=lambda i: embed_scores[i], reverse=True)
            rank_b = {i: r + 1 for r, i in enumerate(bm25_order)}
            rank_e = {i: r + 1 for r, i in enumerate(embed_order)}
            fused = []
            for i, d in enumerate(docs):
                score = 1.0 / (_RRF_K + rank_b[i]) + 1.0 / (_RRF_K + rank_e[i])
                fused.append((d.path, score, "fusion"))
            fused.sort(key=lambda x: x[1], reverse=True)
            ranked = fused
        else:
            ranked = [(docs[i].path, float(bm25_scores[i]), "bm25") for i in bm25_order]

        targets: list[FileTarget] = []
        for path, score, source in ranked[: self.top_k_files]:
            if score <= 0:
                continue
            targets.append(FileTarget(path=path, score=score, source=source))
        return targets

    # ------------------------------------------------------------------
    # Phase 2: structural parse + suspect classification
    # ------------------------------------------------------------------

    async def _phase_structural(
        self,
        bug_report: str,
        targets: list[FileTarget],
        emit: Callable[[str, str], None],
    ) -> list[FunctionSuspect]:
        structures: dict[str, list[dict]] = {}
        for t in targets:
            full = self.repo_path / t.path
            if not full.is_file():
                continue
            try:
                with full.open("r", encoding="utf-8", errors="replace") as handle:
                    source = handle.read(self.max_file_chars_for_ir)
            except Exception:
                continue
            symbols = self._symbols_for_text(source, full, t.path)
            structures[t.path] = symbols

        if not structures:
            return []

        prompt = self._build_structure_prompt(bug_report, structures)
        messages = [
            {"role": "system", "content": SYSTEM_FAULT_SUSPECT_CLASSIFIER},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = await self._llm(messages, max_tokens=512)
        except Exception as e:
            emit("phase2", f"LLM call failed ({e}); using heuristic fallback")
            return self._heuristic_suspects(structures, bug_report)

        data = extract_json(resp) or {}
        raw = data.get("suspects", []) if isinstance(data, dict) else []
        if not isinstance(raw, list):
            raw = []

        suspects: list[FunctionSuspect] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            file = str(entry.get("file", "")).strip()
            symbol = str(entry.get("symbol", "")).strip()
            if not file or not symbol:
                continue
            matched = self._match_symbol(file, symbol, structures)
            if matched is None:
                # hallucinated symbol — drop it
                continue
            suspects.append(FunctionSuspect(
                file=matched["file"],
                symbol=matched["symbol"],
                kind=matched.get("kind", "function"),
                line=matched.get("line", 0),
                end_line=matched.get("end_line", matched.get("line", 0)),
                args=matched.get("args", []),
                reason=str(entry.get("reason", ""))[:300],
                confidence=float(entry.get("confidence", 0.0) or 0.0),
            ))

        if not suspects:
            emit("phase2", "LLM returned no usable suspects; using heuristic fallback")
            return self._heuristic_suspects(structures, bug_report)

        suspects.sort(key=lambda s: s.confidence, reverse=True)
        return suspects[: self.top_suspects]

    def _build_structure_prompt(self, bug_report: str, structures: dict[str, list[dict]]) -> str:
        lines = ["## BUG REPORT", bug_report.strip(), "", "## FILE STRUCTURES"]
        for path, symbols in structures.items():
            lines.append(f"\n### {path}")
            if not symbols:
                lines.append("  (no parseable symbols; whole file is one unit)")
                continue
            for sym in symbols:
                indent = "    " if sym.get("parent") else "  "
                args = sym.get("args") or []
                arg_str = f"({', '.join(args)})" if args else "()"
                lines.append(
                    f"{indent}{sym['line']:>4}-{sym.get('end_line', sym['line']):<4} "
                    f"{sym.get('kind', 'function')} {sym.get('name', '')}{arg_str}"
                )
        lines.append("")
        lines.append("Identify the suspect functions/methods/classes most likely to"
                     " contain the root cause of the bug. Return JSON only.")
        return "\n".join(lines)

    def _match_symbol(
        self, file: str, symbol: str, structures: dict[str, list[dict]]
    ) -> dict | None:
        syms = structures.get(file)
        if syms is None:
            # try fuzzy file match
            for k, v in structures.items():
                if k.endswith(file) or file.endswith(k):
                    syms = v
                    file = k
                    break
        if not syms:
            return None
        target = symbol.strip()
        target_lower = target.lower()
        # exact name match (handles "Class.method")
        for sym in syms:
            if sym.get("name", "").lower() == target_lower:
                return {"file": file, "symbol": sym["name"], **sym}
        # substring match
        for sym in syms:
            name = sym.get("name", "")
            if name and (target_lower in name.lower() or name.lower() in target_lower):
                return {"file": file, "symbol": name, **sym}
        return None

    # ------------------------------------------------------------------
    # Phase 3: line-level edit window
    # ------------------------------------------------------------------

    async def _phase_line_window(
        self,
        bug_report: str,
        suspects: list[FunctionSuspect],
        emit: Callable[[str, str], None],
    ) -> list[LineWindow]:
        candidates = self._collect_candidate_sources(suspects)
        if not candidates:
            return []

        prompt = self._build_line_prompt(bug_report, candidates)
        messages = [
            {"role": "system", "content": SYSTEM_FAULT_LINE_PINPOINT},
            {"role": "user", "content": prompt},
        ]
        try:
            resp = await self._llm(messages, max_tokens=512)
        except Exception as e:
            emit("phase3", f"LLM call failed ({e}); using symbol spans as windows")
            return self._fallback_windows(suspects)

        data = extract_json(resp) or {}
        raw = data.get("windows", []) if isinstance(data, dict) else []
        if not isinstance(raw, list):
            raw = []

        windows: list[LineWindow] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            file = str(entry.get("file", "")).strip()
            symbol = str(entry.get("symbol", "")).strip()
            span = self._lookup_span(suspects, file, symbol)
            if span is None:
                continue
            start = int(entry.get("start_line") or span.line)
            end = int(entry.get("end_line") or span.end_line or start)
            # clamp to the function's actual span
            start = max(span.line, min(start, span.end_line or span.line))
            end = max(start, min(end, span.end_line or start))
            windows.append(LineWindow(
                file=span.file,
                symbol=span.symbol,
                start_line=start,
                end_line=end,
                rationale=str(entry.get("rationale", ""))[:300],
                confidence=float(entry.get("confidence", 0.0) or 0.0),
            ))

        if not windows:
            emit("phase3", "LLM returned no usable windows; using symbol spans")
            return self._fallback_windows(suspects)
        return windows

    def _collect_candidate_sources(self, suspects: list[FunctionSuspect]) -> list[dict]:
        out: list[dict] = []
        for s in suspects:
            full = self.repo_path / s.file
            if not full.is_file():
                continue
            try:
                lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            start = max(1, s.line)
            end = s.end_line or s.line
            if end < start:
                end = start
            end = min(end, len(lines))
            start = min(start, end)
            snippet = "\n".join(
                f"{i + 1:4d}: {lines[i]}"
                for i in range(start - 1, end)
            )
            out.append({
                "file": s.file,
                "symbol": s.symbol,
                "line": start,
                "end_line": end,
                "source": snippet,
            })
        return out

    def _build_line_prompt(self, bug_report: str, candidates: list[dict]) -> str:
        lines = ["## BUG REPORT", bug_report.strip(), "", "## CANDIDATE FUNCTIONS"]
        for i, c in enumerate(candidates, 1):
            lines.append(f"\n### Candidate {i}: {c['file']} :: {c['symbol']} "
                         f"(lines {c['line']}-{c['end_line']})")
            lines.append("```")
            lines.append(c["source"])
            lines.append("```")
        lines.append("")
        lines.append("Pinpoint the exact contiguous line range within each candidate"
                     " function where the bug most likely lives. Return JSON only.")
        return "\n".join(lines)

    def _lookup_span(self, suspects: list[FunctionSuspect], file: str, symbol: str) -> FunctionSuspect | None:
        for s in suspects:
            if s.file == file and s.symbol == symbol:
                return s
        # fuzzy
        for s in suspects:
            if (file and (file in s.file or s.file in file)) and (
                symbol and (symbol in s.symbol or s.symbol in symbol)
            ):
                return s
        return suspects[0] if suspects else None

    def _pick_primary(self, windows: list[LineWindow]) -> LineWindow | None:
        valid = [w for w in windows if w.end_line >= w.start_line > 0]
        if not valid:
            return None
        valid.sort(key=lambda w: w.confidence, reverse=True)
        return valid[0]

    # ------------------------------------------------------------------
    # Snippet construction
    # ------------------------------------------------------------------

    def _build_snippet(self, window: LineWindow) -> str:
        full = self.repo_path / window.file
        if not full.is_file():
            return ""
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return ""
        total = len(lines)
        if total == 0:
            return ""
        # Center the snippet on the pinpointed window, expand to snippet_lines.
        center = (window.start_line + window.end_line) // 2
        half = self.snippet_lines // 2
        start = max(1, center - half)
        end = min(total, start + self.snippet_lines - 1)
        # If we hit the bottom, slide up to use the full budget.
        if end - start + 1 < self.snippet_lines:
            start = max(1, end - self.snippet_lines + 1)
        body = "\n".join(
            f"{i + 1:4d}: {lines[i]}"
            for i in range(start - 1, end)
        )
        header = (f"# {window.file} (lines {start}-{end} of {total})\n"
                  f"# pinpointed window: {window.start_line}-{window.end_line}")
        return f"{header}\n{body}"

    # ------------------------------------------------------------------
    # Helpers: symbol extraction, LLM call, fallbacks
    # ------------------------------------------------------------------

    def _symbols_for_text(self, source: str, full: Path, rel: str) -> list[dict]:
        if full.suffix.lower() == ".py":
            try:
                tree = ast.parse(source)
            except (SyntaxError, Exception):
                return self._generic_symbols(source)
            symbols: list[dict] = []
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
        return self._generic_symbols(source)

    def _generic_symbols(self, source: str) -> list[dict]:
        """Best-effort signature extraction for non-Python languages."""
        lines = source.splitlines()
        matches: list[tuple[int, str, str]] = []  # (line_no, kind, name)
        for i, line in enumerate(lines, 1):
            m = _GENERIC_SIG_RE.match(line)
            if not m:
                continue
            name = None
            kind = "function"
            for gname, gkname in (
                ("pyn", "function"), ("jsn", "function"), ("an", "function"),
                ("gon", "function"), ("rsn", "function"), ("rbn", "function"),
                ("phpn", "function"), ("swiftn", "function"), ("luan", "function"),
                ("csn", "function"), ("clsn", "class"), ("structn", "class"),
                ("ifacen", "class"),
            ):
                val = m.groupdict().get(gname)
                if val:
                    name = val
                    kind = gkname
                    break
            if name:
                matches.append((i, kind, name))
        if not matches:
            return []
        symbols: list[dict] = []
        for idx, (line_no, kind, name) in enumerate(matches):
            end_line = (matches[idx + 1][0] - 1) if idx + 1 < len(matches) else len(lines)
            if end_line < line_no:
                end_line = line_no
            symbols.append({
                "name": name,
                "kind": kind,
                "line": line_no,
                "end_line": end_line,
                "args": [],
                "parent": None,
            })
        return symbols

    async def _llm(self, messages: list[dict], max_tokens: int = 1024) -> str:
        if self.router is None:
            return ""
        resp = await asyncio.wait_for(
            self.router.generate(
                role="localize",
                messages=messages,
                max_tokens=max_tokens,
                disable_reasoning=True,
            ),
            timeout=self.llm_timeout_seconds,
        )
        return resp.content or ""

    def _fallback_suspects(self, targets: list[FileTarget]) -> list[FunctionSuspect]:
        suspects: list[FunctionSuspect] = []
        for t in targets[: self.top_suspects]:
            full = self.repo_path / t.path
            line_count = 0
            if full.is_file():
                try:
                    line_count = len(full.read_text(encoding="utf-8", errors="replace").splitlines())
                except Exception:
                    pass
            suspects.append(FunctionSuspect(
                file=t.path,
                symbol="<module>",
                kind="module",
                line=1,
                end_line=line_count,
                reason="no LLM available; whole-file fallback",
                confidence=t.score,
            ))
        return suspects

    def _fallback_windows(self, suspects: list[FunctionSuspect]) -> list[LineWindow]:
        windows: list[LineWindow] = []
        for s in suspects:
            start = s.line or 1
            end = s.end_line or start
            windows.append(LineWindow(
                file=s.file,
                symbol=s.symbol,
                start_line=start,
                end_line=end,
                rationale="symbol-span fallback (no LLM pinpoint)",
                confidence=max(0.0, s.confidence),
            ))
        return windows

    def _heuristic_suspects(
        self, structures: dict[str, list[dict]], bug_report: str
    ) -> list[FunctionSuspect]:
        """Fallback when the LLM call fails: score symbols by keyword overlap."""
        terms = set(_tokenize(bug_report))
        scored: list[tuple[float, str, dict]] = []
        for path, syms in structures.items():
            for sym in syms:
                name_tokens = set(_split_identifier(sym.get("name", "")))
                overlap = len(terms & name_tokens)
                # small body-size bias toward smaller, focused functions
                size = max(1, (sym.get("end_line", 0) or 0) - (sym.get("line", 0) or 0) or 1)
                score = overlap * 2.0 - 0.001 * size
                scored.append((score, path, sym))
        scored.sort(key=lambda x: x[0], reverse=True)
        suspects: list[FunctionSuspect] = []
        for score, path, sym in scored[: self.top_suspects]:
            suspects.append(FunctionSuspect(
                file=path,
                symbol=sym.get("name", ""),
                kind=sym.get("kind", "function"),
                line=sym.get("line", 0),
                end_line=sym.get("end_line", sym.get("line", 0)),
                args=sym.get("args", []),
                reason="heuristic keyword-overlap fallback",
                confidence=min(1.0, max(0.0, score / 4.0)),
            ))
        return suspects
