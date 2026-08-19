from __future__ import annotations

import json
import re
from typing import Any

from .types import TierConfig


_ERROR_LINE_RE = re.compile(
    r"(?:"
    r"^\s*Traceback\b"                              # Python traceback header
    r"|^\s*\w*(?:Error|Exception|Warning)\b\s*:"    # "SyntaxError:", "ValueError:", "DeprecationWarning:"
    r"|^\s*\[\s*(?:ERROR|WARN|CRITICAL)\s*\]"        # [ERROR], [WARN], [CRITICAL]
    r"|^(?:ERROR|FAILED|FAIL)\b"                     # ERROR:, FAILED, FAIL:
    r"|^\s*(?:STDERR|stderr)\b"                      # stderr markers
    r"|Permission denied"
    r"|exit code:?\s*[1-9]\d*"                       # nonzero exit code only
    r"|ModuleNotFoundError|FileNotFoundError"
    r")"
)

_SHELL_STDERR_RE = re.compile(
    r"(?:"
    r"^\s*(?:STDERR|stderr)\b"
    r"|^\s*Traceback\b"
    r"|^\s*\w*(?:Error|Exception)\b\s*:"
    r"|^(?:ERROR|FAILED|FAIL)\b"
    r")"
)


class Distiller:
    SCHEMAS = {  # used somewhere below i think
        "pytest_output": {
            "preserve": ["failed_tests", "error_messages", "summary_line", "traceback_last_line"],
            "drop": ["passed_tests", "warnings", "captured_stdout"],
        },
        "shell_output": {
            "preserve": ["exit_code", "stderr", "last_20_lines"],
            "drop": ["repeated_lines"],
        },
        "file_listing": {
            "preserve": ["paths"],
            "max_items": 50,
            "drop": [],
        },
        "linter_output": {
            "preserve": ["error_code", "message", "file", "line"],
            "drop": ["style_warnings"],
        },
        "api_response": {
            "preserve": ["status", "data"],
            "max_chars": 2000,
            "drop": ["metadata", "pagination"],
        },
        "code_file": {
            "preserve": ["structure"],
            "drop": ["comments", "blank_lines"],
        },
    }

    def __init__(self, tier_config: TierConfig | None = None):
        self.tier = tier_config or TierConfig(name="balanced")

    def distill(self, data: str, schema_name: str | None = None, target_tokens: int = 500, hint: str = "") -> str:
        if not data:
            return ""
        if not schema_name:
            schema_name = self._detect_schema(data)
        if schema_name == "pytest_output":
            return self._distill_pytest(data, target_tokens)
        elif schema_name == "shell_output":
            return self._distill_shell(data, target_tokens)
        elif schema_name == "linter_output":
            return self._distill_linter(data, target_tokens)
        elif schema_name == "file_listing":
            return self._distill_listing(data, target_tokens)
        return self._generic_distill(data, target_tokens)

    def distill_to_signal(self, data: str, hint: str = "") -> str:
        if len(data) < 200:
            return data
        d = self.distill(data, hint=hint)
        lines = d.strip().splitlines()
        if len(lines) <= 3:
            return d
        return lines[0][:200]

    def distill_for_history(self, data: str, tool_name: str) -> str:
        mode = self.tier.history_distill
        if mode == "ultra":
            return self._ultra_signal(data, tool_name)
        elif mode == "tiered":
            return self._tiered_signal(data, tool_name)
        elif mode == "gradual":
            return self._gradual_signal(data, tool_name)
        return self._generic_distill(data, 500)

    def _preserve_error_lines(self, lines: list[str], max_lines: int = 15) -> list[str]:
        # Only flag lines that are genuine error/failure indicators. A bare
        # substring like "error"/"Error" appears constantly in source code
        # (e.g. `err = chunk.error`) and must NOT be treated as a failure
        # signal — doing so pollutes read_file/grep output with spurious
        # "SIGNAL:" prefixes and discards the real content.
        preserved = []
        for line in lines[-max_lines:]:
            if _ERROR_LINE_RE.search(line):
                preserved.append(line)
        return preserved

    def _ultra_signal(self, data: str, tool_name: str) -> str:
        if not data:
            return ""
        lines = data.splitlines()

        error_lines = self._preserve_error_lines(lines)
        if error_lines:
            return " | ".join(error_lines[:3])

        if tool_name == "read_file":
            return f"<read_file lines={len(lines)}/>"
        if tool_name in ("edit_file", "edit_lines", "aider_edit"):
            return "Edit applied."
        if tool_name == "create_file":
            return "File created."
        if tool_name == "delete_file":
            return "File deleted."
        if tool_name == "run_command":
            exit_match = re.search(r"[Ee]xit code:? (\d+)", data)
            code = exit_match.group(1) if exit_match else "?"
            if code != "0":
                tail = "\n".join(lines[-5:])
                return f"Command exited {code}.\n{tail}"
            return f"Command exited {code}."
        if tool_name in ("grep_code", "find_symbols"):
            count = len([l for l in lines if l.strip()])
            return f"Found {count} matches."
        if tool_name in ("glob_files", "list_dir"):
            count = len([l for l in lines if l.strip()])
            return f"Found {count} items."
        if tool_name == "get_structure":
            count = len([l for l in lines if l.strip()])
            return f"Structure: {count} symbols."
        if tool_name == "fetch_url":
            return "Fetched URL."
        first = lines[0][:120] if lines else ""
        return first or "Done."

    def _tiered_signal(self, data: str, tool_name: str) -> str:
        if not data:
            return ""
        lines = data.splitlines()
        error_lines = self._preserve_error_lines(lines)
        if error_lines:
            error_prefix = "SIGNAL: " + " | ".join(error_lines[:3]) + "\n"

        if tool_name == "read_file":
            if len(lines) <= 3:
                return data
            first = "\n".join(lines[:3])
            result = f"{first}\n... ({len(lines)} lines total)"
            if error_lines:
                result = error_prefix + result
            return result
        if tool_name in ("edit_file", "edit_lines", "aider_edit"):
            result = self._extract_edit_summary(data)
            if error_lines:
                result = error_prefix + result
            return result
        if tool_name == "run_command":
            result = self._distill_shell(data, 150)
            if error_lines:
                result = error_prefix + result
            return result
        if tool_name in ("grep_code", "find_symbols"):
            lines = [l for l in data.splitlines() if l.strip()]
            if len(lines) <= 10:
                return data
            return "\n".join(lines[:10]) + f"\n... ({len(lines)} total matches)"
        if tool_name in ("glob_files", "list_dir"):
            lines = [l for l in data.splitlines() if l.strip()]
            if len(lines) <= 30:
                return data
            return "\n".join(lines[:30]) + f"\n... ({len(lines)} total items)"
        if tool_name == "fetch_url":
            return data[:800]
        return self._generic_distill(data, 500)

    def _gradual_signal(self, data: str, tool_name: str) -> str:
        if not data:
            return ""
        if len(data) < 4000:
            return data
        if tool_name == "read_file":
            lines = data.splitlines()
            keep = min(60, len(lines))
            first = "\n".join(lines[:keep])
            if len(lines) > keep:
                return f"{first}\n... ({len(lines) - keep} more lines)"
            return first
        if tool_name in ("grep_code", "find_symbols"):
            lines = [l for l in data.splitlines() if l.strip()]
            if len(lines) <= 20:
                return data
            return "\n".join(lines[:20]) + f"\n... ({len(lines)} total matches)"
        if tool_name == "run_command":
            return self._distill_shell(data, 400)
        return self._generic_distill(data, 800)

    def _extract_edit_summary(self, data: str) -> str:
        lines = data.splitlines()
        summary = []
        for line in lines:
            if line.startswith("Applied ") or line.startswith("Replaced lines ") or line.startswith("OK:"):
                summary.append(line)
        if summary:
            return "\n".join(summary)
        return data[:300]

    def _detect_schema(self, data: str) -> str:
        lower = data[:1000].lower()
        if "passed" in lower and "failed" in lower and ("test" in lower or "error" in lower):
            return "pytest_output"
        if "exit code" in lower or "stderr" in lower or "command" in lower:
            return "shell_output"
        if re.search(r"\.(py|js|ts):\d+:\d+: [EFW]", lower):
            return "linter_output"
        if lower.count("\n") > 5 and all("/" in l or "\\" in l or "." in l for l in data.strip().splitlines()[:10] if l.strip()):
            return "file_listing"
        return "generic"

    def _distill_pytest(self, data: str, target_tokens: int) -> str:
        lines = data.splitlines()
        result_parts = []
        summary_line = ""
        failures = []
        in_failure = False
        current_failure: list[str] = []

        for line in lines:
            if "FAILED" in line:
                failures.append(line.strip())
            if line.startswith("=== ") and "failed" in line.lower():
                summary_line = line.strip()
            if line.startswith("E   ") or line.startswith("    assert "):
                current_failure.append(line.rstrip())
                in_failure = True
            elif in_failure and line.strip() == "":
                if current_failure:
                    failures.extend(current_failure[-3:])
                    current_failure = []
                in_failure = False

        if summary_line:
            result_parts.append(f"Summary: {summary_line}")
        if failures:
            result_parts.append("Failures:")
            budget = target_tokens * 4
            for f in failures:
                if len("\n".join(result_parts)) > budget:
                    result_parts.append(f"  ... ({len(failures)} total, showing first few)")
                    break
                result_parts.append(f"  {f}")
        passed_match = re.search(r"(\d+) passed", data)
        if passed_match:
            result_parts.insert(0, f"Passed: {passed_match.group(1)}")
        return "\n".join(result_parts) if result_parts else data[:target_tokens * 4]

    def _distill_shell(self, data: str, target_tokens: int) -> str:
        lines = data.splitlines()
        exit_match = re.search(r"[Ee]xit code:? (\d+)", data)
        # Use a precise stderr/error matcher: a bare "error" substring is far
        # too common in command output (e.g. grepping code that references
        # `chunk.error`) and produces misleading "Errors:" sections.
        stderr_lines = [l for l in lines if _SHELL_STDERR_RE.search(l)]

        parts = []
        if exit_match:
            parts.append(f"Exit code: {exit_match.group(1)}")
        if stderr_lines:
            parts.append("Errors:\n" + "\n".join(stderr_lines[:5]))
        tail = lines[-20:]
        if tail and len(parts) < target_tokens // 4:
            parts.append("Last output:\n" + "\n".join(tail))
        return "\n".join(parts) if parts else data[:target_tokens * 4]

    def _distill_linter(self, data: str, target_tokens: int) -> str:
        lines = data.splitlines()
        errors = [l.strip() for l in lines if re.search(r"[EF]\d+", l) or "error" in l.lower()]
        if errors:
            return f"Linter ({len(errors)} issues):\n" + "\n".join(errors[:20])
        return data[:target_tokens * 4]

    def _distill_listing(self, data: str, target_tokens: int) -> str:
        lines = data.strip().splitlines()
        if len(lines) > 50:
            return "\n".join(lines[:40]) + f"\n... ({len(lines) - 40} more files)"
        return data[:target_tokens * 4]

    def _generic_distill(self, data: str, target_tokens: int) -> str:
        max_chars = target_tokens * 4
        if len(data) <= max_chars:
            return data
        return data[:max_chars] + "\n... (truncated)"

    def mask_observation(self, data: str, focus: str = "") -> str:
        if not focus or len(data) < 500:
            return data
        lines = data.splitlines()
        focus_terms = focus.lower().split()
        scored = []
        for i, line in enumerate(lines):
            score = sum(1 for t in focus_terms if t in line.lower())
            scored.append((i, score, line))

        high = [(i, line) for i, score, line in scored if score > 0]
        if not high:
            return data[:2000]

        selected = set()
        for i, line in high:
            for offset in range(-2, 3):
                idx = i + offset
                if 0 <= idx < len(lines):
                    selected.add(idx)

        result = []
        prev = -1
        for i in sorted(selected):
            if prev >= 0 and i > prev + 1:
                result.append(f"  ... ({i - prev - 1} lines skipped) ...")
            result.append(lines[i])
            prev = i
        return "\n".join(result)
