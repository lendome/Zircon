from __future__ import annotations

import ast
import difflib
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .types import TierConfig

logger = logging.getLogger("agent.edit_engine")


@dataclass
class EditBlock:
    path: str
    search: str
    replace: str

    @staticmethod
    def from_aider_format(text: str) -> list[EditBlock]:
        blocks = []
        ext_pat = r'\.(?:py|js|ts|rs|go|java|yaml|yml|json|toml|md|txt|sh|rb|c|cpp|h|hpp|cs|php|sql|html|css|jsx|tsx|vue|svelte)'
        noext_pat = r'(?:Dockerfile|Makefile|docker-compose\.yml|\.gitignore|\.env|\.editorconfig|\.prettierrc)'
        pattern = re.compile(
            rf'(^[^\n]*(?:{ext_pat}|{noext_pat}))\n'
            r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE',
            re.MULTILINE | re.DOTALL,
        )
        for m in pattern.finditer(text):
            blocks.append(EditBlock(path=m.group(1).strip(), search=m.group(2), replace=m.group(3)))
        return blocks


@dataclass
class EditResult:
    success: bool
    method: str = ""
    error: str = ""
    path: str = ""
    new_content: str = ""
    old_content: str = ""
    verified: bool = False
    confidence: float = 0.0
    matched_text: str = ""
    start: int = -1
    end: int = -1
    original_hash: str = ""


class EditEngine:
    def __init__(self, tier_config: TierConfig | None = None):
        self.tier = tier_config or TierConfig(name="balanced")
        self.max_repair_attempts = self.tier.edit_repair_attempts

    def apply_search_replace(
        self, file_path: Path, search: str, replace: str, context: str = ""
    ) -> EditResult:
        proposal = self.propose_search_replace(file_path, search, replace, context)
        if not proposal.success:
            return proposal
        return self.apply_proposal(file_path, proposal)

    def propose_search_replace(
        self, file_path: Path, search: str, replace: str, context: str = ""
    ) -> EditResult:
        """Resolve and syntax-check an edit without mutating the file."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")

        if not search.strip():
            return EditResult(success=False, error="Empty search text")

        matchers = [
            ("exact", self._exact_match),
            ("fuzzy", self._fuzzy_match),
            ("whitespace_normalized", self._ws_normalized_match),
            ("ast_aware", self._ast_aware_match),
        ]

        for method_name, matcher in matchers:
            result = matcher(content, search, file_path, context)
            if result is None:
                continue
            start, end, matched_text = result
            new_content = content[:start] + replace + content[end:]
            confidence = self._match_confidence(method_name, matched_text, search)

            logger.debug("apply_search_replace: %s matched in %s", method_name, file_path.name)
            # Syntax-gated: reject the edit if it produces invalid syntax.
            # Do NOT attempt auto-fixes — the agent must produce valid code.
            if not self._verify(file_path, new_content):
                return EditResult(
                    success=False,
                    error=f"Syntax error in proposed edit for {file_path.name}. "
                          f"The replacement text produces invalid syntax. "
                          f"Please revise the edit and try again.",
                    path=str(file_path),
                    old_content=content,
                    new_content=new_content,
                )

            return EditResult(
                success=True, method=method_name, path=str(file_path),
                new_content=new_content, old_content=content, verified=True,
                confidence=confidence, matched_text=matched_text,
                start=start, end=end,
                original_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        return EditResult(
            success=False,
            error=f"No match found for search text in {file_path.name}. "
                  f"Try reading the file first to get exact content.",
            path=str(file_path),
        )

    def apply_proposal(self, file_path: Path, proposal: EditResult) -> EditResult:
        """Apply a verified proposal only if its source file is unchanged."""
        try:
            current = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")
        current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if not proposal.success or current_hash != proposal.original_hash:
            return EditResult(
                success=False,
                error="Edit preview is stale because the file changed; recompute the edit.",
                path=str(file_path),
            )
        if not self._verify(file_path, proposal.new_content):
            return EditResult(success=False, error="Syntax error in proposed edit", path=str(file_path))
        try:
            file_path.write_text(proposal.new_content, encoding="utf-8")
            return proposal
        except Exception as e:
            return EditResult(success=False, error=f"Write failed: {e}")

    @staticmethod
    def _match_confidence(method: str, matched_text: str, search: str) -> float:
        if method == "exact":
            return 1.0
        if method == "whitespace_normalized":
            return 0.98
        return round(difflib.SequenceMatcher(None, matched_text, search).ratio(), 3)

    def apply_line_edit(self, file_path: Path, start: int, end: int, content: str) -> EditResult:
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")

        lines = text.splitlines(keepends=True)
        total = len(lines)
        s = max(1, start) - 1
        e = max(1, end)

        if s >= total:
            return EditResult(success=False, error=f"Start line {start} > file length ({total})")

        if not content.endswith("\n") and e <= len(lines) and lines[min(e - 1, len(lines) - 1)].endswith("\n"):
            content += "\n"

        new_lines = lines[:s] + [content] + lines[e:]
        new_text = "".join(new_lines)

        # Syntax-gated: reject the edit if it produces invalid syntax.
        # Do NOT attempt auto-fixes — the agent must produce valid code.
        if not self._verify(file_path, new_text):
            return EditResult(
                success=False,
                error="Syntax error after edit. The proposed line replacement produces invalid syntax. Please revise and try again.",
                path=str(file_path),
                old_content=text,
                new_content=new_text,
            )

        try:
            file_path.write_text(new_text, encoding="utf-8")
            return EditResult(
                success=True, method="line_range", path=str(file_path),
                new_content=new_text, old_content=text, verified=True,
            )
        except Exception as e:
            return EditResult(success=False, error=f"Write failed: {e}")

    def apply_ast_replace(self, file_path: Path, symbol_name: str, new_source: str) -> EditResult:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")

        if file_path.suffix.lower() != ".py":
            return EditResult(success=False, error="AST replacement only supported for Python files")

        tree = ast.parse(content)
        replacement_done = False
        new_content = content

        for node in ast.walk(tree):
            names_to_check = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names_to_check.append(node.name)
                if hasattr(node, "parent_class"):
                    names_to_check.append(f"{node.parent_class}.{node.name}")
            elif isinstance(node, ast.ClassDef):
                names_to_check.append(node.name)

            if symbol_name in names_to_check:
                start_line = node.lineno
                end_line = getattr(node, "end_lineno", node.lineno)
                lines = new_content.splitlines(keepends=True)
                if start_line <= len(lines) and end_line <= len(lines):
                    original_first_line = lines[start_line - 1]
                    orig_indent = re.match(r'^(\s*)', original_first_line).group(1)

                    rep_lines = new_source.splitlines()
                    if rep_lines:
                        min_indent = float('inf')
                        for rl in rep_lines:
                            if rl.strip():
                                indent = len(rl) - len(rl.lstrip())
                                min_indent = min(min_indent, indent)
                        if min_indent == float('inf'):
                            min_indent = 0

                        normalized = []
                        for rl in rep_lines:
                            if rl.strip():
                                normalized.append(orig_indent + rl[min_indent:])
                            else:
                                normalized.append('')
                        replacement = "\n".join(normalized)
                    else:
                        replacement = new_source

                    if not replacement.endswith("\n"):
                        replacement += "\n"
                    lines = lines[: start_line - 1] + [replacement] + lines[end_line:]
                    new_content = "".join(lines)
                    replacement_done = True
                    break

        if not replacement_done:
            return EditResult(success=False, error=f"Symbol '{symbol_name}' not found")

        if self._verify(file_path, new_content):
            try:
                file_path.write_text(new_content, encoding="utf-8")
                return EditResult(
                    success=True, method="ast_replace", path=str(file_path),
                    new_content=new_content, old_content=content, verified=True,
                )
            except Exception as e:
                return EditResult(success=False, error=f"Write failed: {e}")

        return EditResult(success=False, error="Syntax error after AST replacement")

    def apply_aider_blocks(self, text: str, repo_path: Path) -> list[EditResult]:
        blocks = EditBlock.from_aider_format(text)
        if not blocks:
            return []
        results = []
        for block in blocks:
            target = repo_path / block.path
            if not target.is_file():
                results.append(EditResult(success=False, error=f"File not found: {block.path}", path=block.path))
                continue
            result = self.apply_search_replace(target, block.search, block.replace)
            result.path = block.path
            results.append(result)
        return results

    def _exact_match(
        self, content: str, search: str, file_path: Path, context: str = ""
    ) -> tuple[int, int, str] | None:
        if not context:
            idx = content.find(search)
            if idx != -1:
                return idx, idx + len(search), search
            return None

        # Search text occurs in multiple places. Use the surrounding text of each
        # occurrence as context to pick the one that best matches the caller's hint.
        best: tuple[int, int, str] | None = None
        best_score = -1.0
        start = 0
        while True:
            idx = content.find(search, start)
            if idx == -1:
                break
            window = self._context_window(content, idx, len(search))
            score = difflib.SequenceMatcher(None, window, context).ratio()
            if score > best_score:
                best_score = score
                best = (idx, idx + len(search), search)
            start = idx + len(search)
        return best

    def _context_window(self, content: str, idx: int, length: int) -> str:
        """Return the match plus a little surrounding text for context matching."""
        before = max(0, idx - 80)
        after = min(len(content), idx + length + 80)
        return content[before:after]

    def _fuzzy_match(
        self, content: str, search: str, file_path: Path, context: str = ""
    ) -> tuple[int, int, str] | None:
        search_lines = search.splitlines()
        content_lines = content.splitlines(keepends=True)
        if len(search_lines) < 2:
            return None

        window = len(search_lines)
        best_ratio = 0.0
        best_start = 0
        best_end = 0

        for i in range(max(1, len(content_lines) - window + 1)):
            window_text = "".join(content_lines[i : i + window])
            ratio = difflib.SequenceMatcher(None, window_text, search).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = sum(len(ln) for ln in content_lines[:i])
                best_end = best_start + len(window_text)

        if best_ratio >= 0.80:
            return best_start, best_end, content[best_start:best_end]
        return None

    def _ws_normalized_match(self, content: str, search: str, file_path: Path, context: str = "") -> tuple[int, int, str] | None:
        def norm(text: str) -> str:
            return "\n".join(line.strip() for line in text.splitlines())

        norm_content = norm(content)
        norm_search = norm(search)

        idx = norm_content.find(norm_search)
        if idx == -1:
            return None

        content_lines = content.splitlines(keepends=True)
        norm_search_lines = norm_search.splitlines()

        running = 0
        start_line = 0
        for i, line in enumerate(content_lines):
            stripped_len = len(line.strip())
            if running + stripped_len >= idx:
                start_line = i
                break
            running += stripped_len + 1

        end_line = start_line + len(norm_search_lines)
        start_char = sum(len(ln) for ln in content_lines[:start_line])
        end_char = sum(len(ln) for ln in content_lines[:end_line])
        return start_char, end_char, content[start_char:end_char]

    def _ast_aware_match(self, content: str, search: str, file_path: Path, context: str = "") -> tuple[int, int, str] | None:
        if file_path.suffix.lower() != ".py":
            return None
        first_line = search.splitlines()[0].strip() if search.splitlines() else ""
        name_match = re.match(r"(?:async\s+)?(?:def|class)\s+(\w+)", first_line)
        if not name_match:
            return None

        symbol_name = name_match.group(1)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return None

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == symbol_name:
                    start_line = node.lineno
                    end_line = getattr(node, "end_lineno", node.lineno)
                    lines = content.splitlines(keepends=True)
                    start_char = sum(len(ln) for ln in lines[:start_line - 1])
                    end_char = sum(len(ln) for ln in lines[:end_line])
                    matched = content[start_char:end_char]
                    ratio = difflib.SequenceMatcher(None, matched.strip(), search.strip()).ratio()
                    if ratio >= 0.5:
                        return start_char, end_char, matched

        return None

    def _verify(self, file_path: Path, content: str) -> bool:
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            try:
                ast.parse(content)
                return True
            except SyntaxError:
                return False
        elif suffix == ".json":
            try:
                json.loads(content)
                return True
            except json.JSONDecodeError:
                return False
        elif suffix in (".html", ".htm", ".xhtml", ".xml", ".svg"):
            return self._verify_html(content)
        return True

    @staticmethod
    def _verify_html(content: str) -> bool:
        """Basic HTML structural check using html.parser.

        Detects unbalanced and mismatched tags. Does not inspect the
        text content of <script>/<style> blocks — JS template literals
        and CSS strings may legitimately contain HTML-like markup.
        """
        import html.parser

        _VOID_ELEMENTS = frozenset({
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        })

        class _TagTracker(html.parser.HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack: list[str] = []
                self.errors: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag.lower() in _VOID_ELEMENTS:
                    return
                self.stack.append(tag.lower())

            def handle_endtag(self, tag):
                lowered = tag.lower()
                if lowered in _VOID_ELEMENTS:
                    return
                if not self.stack:
                    self.errors.append(f"Unexpected </{tag}> with empty stack")
                    return
                if self.stack[-1] == lowered:
                    self.stack.pop()
                    return
                # Search deeper in the stack for a match
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i] == lowered:
                        for j in range(len(self.stack) - 1, i, -1):
                            self.errors.append(f"Unclosed <{self.stack[j]}> before </{tag}>")
                            self.stack.pop(j)
                        self.stack.pop()  # pop the matched tag
                        return
                self.errors.append(f"Unexpected </{tag}> (no matching open tag)")

            def handle_startendtag(self, tag, attrs):
                pass  # Self-closing tags like <br/> are fine

        try:
            tracker = _TagTracker()
            tracker.feed(content)
            tracker.close()
            # Report remaining unclosed tags
            for tag in tracker.stack:
                tracker.errors.append(f"Unclosed <{tag}>")
            if tracker.errors:
                logger.debug("HTML validation failed: %s", tracker.errors[:5])
                return False
            return True
        except Exception as e:
            logger.debug("HTML validation error: %s", e)
            return False

    def _try_fix_syntax(self, content: str, file_path: Path) -> str | None:
        """DEPRECATED: auto-fix is no longer used. Edits are rejected on syntax failure."""
        return None

    def parse_aider_blocks(self, text: str) -> list[EditBlock]:
        return EditBlock.from_aider_format(text)
