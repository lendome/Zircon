"""
Approval gate for destructive tool calls.

Classifies tool invocations that would discard git history/working-tree
state or directly mutate a database, and provides a pluggable async gate
that the ToolRegistry consults before executing such calls.

The gate is only armed by the CLI entry points (default/serve/task handlers).
When used as a library (no handler wired), the gate is a no-op that approves
everything — so non-CLI use of the Agent is unaffected.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

logger = None  # local logger to avoid early core import noise
try:  # pragma: no cover - logging is optional here
    import logging as _logging

    logger = _logging.getLogger("agent.tools.approval")
except Exception:  # pragma: no cover
    logger = None


# Tools that shell out and therefore can run dangerous git/db commands.
_SHELL_TOOLS = {"run_command", "shell_start", "run_in_terminal"}

# --- Git patterns that lose work / move history destructively -------------
# Each entry: (compiled regex, human description). Matched case-insensitively
# against the command string. We deliberately focus on operations that DISCARD
# uncommitted changes or rewind HEAD; ordinary `git add`/`git commit`/`git
# checkout <branch>` (switching) are NOT flagged.
_GIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+reset\s+(?:--hard\b|--keep\b|--merge\b)", re.IGNORECASE),
     "git reset --hard discards uncommitted working-tree changes"),
    (re.compile(r"\bgit\s+reset\s+(?:HEAD~|HEAD\^|\w+\^\{?\}?\s+--hard)", re.IGNORECASE),
     "git reset to a prior commit rewrites HEAD destructively"),
    (re.compile(r"\bgit\s+clean\s+(?:-[a-z]*f[a-z]*|--force)", re.IGNORECASE),
     "git clean -f permanently deletes untracked files"),
    (re.compile(r"\bgit\s+checkout\s+--\s", re.IGNORECASE),
     "git checkout -- discards uncommitted changes to tracked files"),
    (re.compile(r"\bgit\s+checkout\s+\.\s*$", re.IGNORECASE),
     "git checkout . discards all uncommitted changes"),
    (re.compile(r"\bgit\s+checkout\s+HEAD\s+--\s", re.IGNORECASE),
     "git checkout HEAD -- discards uncommitted changes"),
    (re.compile(r"\bgit\s+revert\b", re.IGNORECASE),
     "git revert reverts committed changes"),
    (re.compile(r"\bgit\s+restore\s+(?!.*--staged)(?!.*--worktree.*--staged)", re.IGNORECASE),
     "git restore discards uncommitted working-tree changes"),
    (re.compile(r"\bgit\s+stash\s+drop\b", re.IGNORECASE),
     "git stash drop permanently discards a stash"),
    (re.compile(r"\bgit\s+stash\s+clear\b", re.IGNORECASE),
     "git stash clear permanently discards all stashes"),
    (re.compile(r"\bgit\s+reflog\s+expire\b", re.IGNORECASE),
     "git reflog expire prunes reflog entries"),
    (re.compile(r"\bgit\s+update-ref\s+-d\b", re.IGNORECASE),
     "git update-ref -d deletes a reference"),
    (re.compile(r"\bgit\s+branch\s+-D\b"),
     "git branch -D force-deletes a branch"),
    (re.compile(r"\bgit\s+push\s+(?:-\w+\s+)*--force(?:-with-lease)?\b", re.IGNORECASE),
     "git push --force rewrites remote history"),
]

# --- Database mutation patterns ------------------------------------------
# SQL verbs that mutate data or schema. Matched as whole-word tokens so that
# incidental substrings (e.g. "update" inside a URL) don't trip the gate.
_DB_MUTATION_VERBS = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|REPLACE|CREATE|GRANT|REVOKE|MERGE)\b",
    re.IGNORECASE,
)
# Client binaries / invocations that execute SQL.
_DB_CLIENTS = re.compile(
    r"\b(sqlite3|psql|mysql|mariadb|sqlcmd|duckdb|psql)\b",
    re.IGNORECASE,
)
# Direct references to database files.
_DB_FILES = re.compile(r"\b[\w./-]+\.(db|sqlite|sqlite3|duckdb)\b", re.IGNORECASE)


def _command_text(name: str, arguments: dict[str, Any]) -> str:
    """Extract the shell command string from a shell-style tool call."""
    if not isinstance(arguments, dict):
        return ""
    return str(arguments.get("command") or arguments.get("cmd") or "")


def preview_command(name: str, arguments: dict[str, Any], max_len: int = 200) -> str:
    """One-line, length-bounded preview of a tool call for prompt messages."""
    if name in _SHELL_TOOLS:
        return _command_text(name, arguments)[:max_len]
    preview = str(arguments)
    return preview if len(preview) <= max_len else preview[:max_len] + "…"


def classify_dangerous(name: str, arguments: dict[str, Any]) -> str | None:
    """Return a human-readable reason if the call is destructive, else None.

    Covers two categories, both reached only via shell-out tools
    (run_command / shell_start) since those are the only agent-driven paths
    that can run git or SQL:

      1. git operations that lose uncommitted work or rewind history
      2. direct database mutations (SQL DDL/DML against a DB client or file)
    """
    if name not in _SHELL_TOOLS:
        return None

    command = _command_text(name, arguments)
    if not command:
        return None

    for pattern, reason in _GIT_PATTERNS:
        if pattern.search(command):
            return f"{reason}: `{command.strip()}`"

    # Database mutation: a SQL mutation verb together with a DB client binary
    # or a database-file reference.
    if _DB_MUTATION_VERBS.search(command) and (
        _DB_CLIENTS.search(command) or _DB_FILES.search(command)
    ):
        return f"direct database mutation: `{command.strip()}`"

    return None


# Type of the async handler: (tool_name, arguments, reason) -> approved?
ApprovalHandler = Callable[[str, dict[str, Any], str], Awaitable[bool]]


class ApprovalGate:
    """Pluggable async gate consulted before destructive tool execution.

    Defaults to approving everything (no handler / disabled). CLI entry points
    arm it by calling ``enable()`` and ``set_handler(...)``.
    """

    def __init__(self) -> None:
        self.enabled: bool = False
        self._handler: ApprovalHandler | None = None

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def set_handler(self, handler: ApprovalHandler) -> None:
        self._handler = handler

    async def request(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        """Ask the handler to approve a destructive call.

        Returns True (allow) when the gate is disabled or has no handler —
        i.e. when the Agent is used outside the CLI. A handler that raises is
        treated as a denial so a broken prompter can never silently allow a
        destructive op.
        """
        if not self.enabled or self._handler is None:
            return True
        try:
            return bool(await self._handler(name, arguments, reason))
        except Exception as exc:  # pragma: no cover - defensive
            if logger is not None:
                logger.warning("approval handler error for %s: %s (denying)", name, exc)
            return False
