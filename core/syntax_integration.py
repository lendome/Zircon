"""Syntax checker integration — hooks SyntaxChecker into the edit loop.

After every file edit, this module:
1. Auto-runs the SyntaxChecker on the modified file
2. Formats errors into a structured prompt for the LLM
3. Routes minor syntax errors to a cheap fast model for auto-fix
4. Reports back to the main loop with fix status

This is the implementation of "verifiable feedback loops" from the blueprint.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .syntax_checker import SyntaxChecker, SyntaxCheckResult
from .types import TierConfig

logger = logging.getLogger("agent.core.syntax_integration")


_LIGHTWEIGHT_SYNTAX_SUFFIXES = frozenset({
    ".py", ".json", ".yaml", ".yml", ".toml",
    ".html", ".htm", ".xhtml", ".xml", ".svg",
})


def supports_immediate_syntax_check(file_path: str | Path) -> bool:
    """Return whether the path has a lightweight built-in parser/checker."""
    return Path(file_path).suffix.lower() in _LIGHTWEIGHT_SYNTAX_SUFFIXES


def check_file_after_edit(
    file_path: str | Path,
    content: str | None = None,
    repo_path: str | Path | None = None,
    include_warnings: bool = True,
) -> SyntaxCheckResult:
    """Check a file for syntax errors after an edit.

    Args:
        file_path: Path to the modified file
        content: Optional new content (if provided, avoids re-reading from disk)
        repo_path: Repository root (auto-resolved if None)
        include_warnings: Whether to include warnings in output

    Returns:
        SyntaxCheckResult with any issues found
    """
    checker = SyntaxChecker(repo_path=repo_path)
    return checker.check_file(file_path, content=content)


def format_errors_for_loop(
    result: SyntaxCheckResult,
    include_warnings: bool = True,
) -> str:
    """Format syntax errors as a system message for the LLM tool loop.

    Returns an empty string if there are no errors.
    """
    if not result.has_errors and not (include_warnings and result.has_warnings):
        return ""
    return result.format(include_warnings=include_warnings)


def needs_fix_attempt(
    result: SyntaxCheckResult,
    max_attempts: int = 3,
    current_attempt: int = 0,
) -> bool:
    """Determine if the agent should attempt to fix syntax errors.

    Returns True if there are errors and we haven't exceeded max attempts.
    """
    if current_attempt >= max_attempts:
        return False
    return result.has_errors


def is_minor_error(result: SyntaxCheckResult) -> bool:
    """Determine if an error is 'minor' enough for a cheap model.

    Minor errors are:
    - Only 1-2 issues
    - All issues are on adjacent lines (likely a single typo)
    - No issues span more than 5 lines
    - No undefined-name errors (F821) — those require semantic understanding
    """
    if not result.issues:
        return True
    if len(result.issues) > 3:
        return False

    # Check if any issue is a complex semantic error
    for issue in result.issues:
        msg = issue.message.lower()
        rule = issue.rule_id
        # Undefined names, complex logic errors
        if rule in ("F821", "F822", "F823"):
            return False
        if "undefined" in msg or "not defined" in msg:
            return False

    # Check if all issues are on nearby lines
    if len(result.issues) >= 2:
        lines = [i.line for i in result.issues if i.line > 0]
        if lines and (max(lines) - min(lines)) > 5:
            return False

    return True


def build_syntax_fix_prompt(
    file_path: str,
    current_content: str,
    errors: str,
) -> str:
    """Build a concise prompt for the cheap model to fix syntax errors.

    The cheap model receives:
    - The file path
    - The exact syntax errors
    - A request to output SEARCH/REPLACE blocks
    """
    return (
        f"SYNTAX ERROR FIX REQUEST\n\n"
        f"File: {file_path}\n\n"
        f"The following syntax errors were detected:\n{errors}\n\n"
        f"Please output SEARCH/REPLACE blocks (<<<<<<< SEARCH / ======= / >>>>>>> REPLACE) "
        f"to fix ALL of the above errors. Output only the fix blocks, no explanation."
    )


def format_check_result_for_prompt(
    results: dict[str, SyntaxCheckResult],
    include_warnings: bool = True,
) -> str:
    """Format multiple file check results into a single prompt block.

    This is designed to be injected as a <syntax_check> system message in the
    tool loop so the LLM sees exact error lines.
    """
    parts = ["<syntax_check>"]
    for fpath, result in results.items():
        formatted = result.format(include_warnings=include_warnings)
        if formatted:
            parts.append(formatted)
    parts.append("</syntax_check>")
    return "\n".join(parts) if len(parts) > 1 else ""
