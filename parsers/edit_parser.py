from __future__ import annotations

import ast
import difflib
from pathlib import Path
from typing import Any

from ..core.types import EditResult


class EditParser:
    def apply(self, file_path: Path, search: str, replace: str) -> EditResult:
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")

        matchers = [
            ("exact", self._exact_match),
            ("fuzzy", self._fuzzy_match),
            ("line_normalized", self._line_normalized_match),
        ]

        for name, matcher in matchers:
            result = matcher(content, search)
            if result is not None:
                start, end = result
                new_content = content[:start] + replace + content[end:]
                verification = self._verify(file_path, new_content)
                if verification:
                    try:
                        file_path.write_text(new_content, encoding="utf-8")
                        return EditResult(success=True, matcher=name, new_content=new_content)
                    except Exception as e:
                        return EditResult(success=False, error=f"Write failed: {e}")
                else:
                    return EditResult(
                        success=False,
                        error="Edit would introduce a syntax error. Rolling back.",
                    )

        return EditResult(
            success=False,
            error=f"No match found for search text. Try reading the file first to get exact content.",
        )

    def apply_lines(self, file_path: Path, start: int, end: int, content: str) -> EditResult:
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return EditResult(success=False, error=f"Cannot read file: {e}")

        lines = text.splitlines(keepends=True)

        s = max(1, start) - 1
        e = max(1, end)

        if s >= len(lines):
            return EditResult(success=False, error=f"Start line {start} exceeds file length ({len(lines)} lines)")

        if not content.endswith("\n") and s < len(lines) and lines[min(e - 1, len(lines) - 1)].endswith("\n"):
            content += "\n"

        new_lines = lines[:s] + [content] + lines[e:]
        new_text = "".join(new_lines)

        verification = self._verify(file_path, new_text)
        if not verification:
            return EditResult(success=False, error="Edit would introduce a syntax error. Rolling back.")

        try:
            file_path.write_text(new_text, encoding="utf-8")
            return EditResult(success=True, matcher="line_range", new_content=new_text)
        except Exception as e:
            return EditResult(success=False, error=f"Write failed: {e}")

    @staticmethod
    def _exact_match(content: str, search: str) -> tuple[int, int] | None:
        idx = content.find(search)
        if idx != -1:
            return idx, idx + len(search)
        return None

    @staticmethod
    def _fuzzy_match(content: str, search: str) -> tuple[int, int] | None:
        search_lines = search.splitlines()
        content_lines = content.splitlines(keepends=True)

        if len(search_lines) < 2:
            return None

        best_ratio = 0.0
        best_start = 0
        best_end = 0
        window = len(search_lines)

        for i in range(len(content_lines) - window + 1):
            window_text = "".join(content_lines[i : i + window])
            ratio = difflib.SequenceMatcher(None, window_text, search).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = sum(len(l) for l in content_lines[:i])
                best_end = sum(len(l) for l in content_lines[: i + window])

        if best_ratio >= 0.80:
            return best_start, best_end
        return None

    @staticmethod
    def _line_normalized_match(content: str, search: str) -> tuple[int, int] | None:
        def normalize(text: str) -> str:
            return "\n".join(line.strip() for line in text.splitlines())

        norm_content = normalize(content)
        norm_search = normalize(search)

        idx = norm_content.find(norm_search)
        if idx == -1:
            return None

        char_map = []
        for i, line in enumerate(content.splitlines(keepends=True)):
            for _ in line.strip():
                char_map.append(sum(len(l) for l in content.splitlines(keepends=True)[:i]))
            if line.strip():
                char_map.append(sum(len(l) for l in content.splitlines(keepends=True)[:i]) + len(line.strip()))

        start_char = 0
        running = 0
        for i, line in enumerate(content.splitlines(keepends=True)):
            stripped = line.strip()
            if running == idx:
                start_char = sum(len(l) for l in content.splitlines(keepends=True)[:i])
                break
            running += len(stripped) + 1

        end_search_lines = norm_search.count("\n") + 1
        content_lines = content.splitlines(keepends=True)
        start_line = 0
        running_len = 0
        for i, line in enumerate(content_lines):
            cur_norm = normalize(content[: sum(len(l) for l in content_lines[: i + 1])])
            if len(cur_norm) >= idx + len(norm_search):
                end_char = sum(len(l) for l in content_lines[: i + 1])
                return start_char, end_char

        return None

    @staticmethod
    def _verify(file_path: Path, new_content: str) -> bool:
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            try:
                ast.parse(new_content)
            except SyntaxError:
                return False
        elif suffix == ".json":
            import json
            try:
                json.loads(new_content)
            except json.JSONDecodeError:
                return False
        return True
