from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .base import Tool
from .approval import ApprovalGate, classify_dangerous, preview_command

logger = logging.getLogger("agent.tools.registry")


# --- Command circuit breaker -------------------------------------------------
#
# Shell-syntax failure signatures: the command can NEVER succeed as written
# (wrong shell syntax, missing binary), so re-running it byte-identically is
# guaranteed waste. Everything else may be flaky — soft-blocked only.
_SYNTAX_FAILURE_PATTERNS = (
    re.compile(r"is not recognized as an internal or external command", re.I),
    re.compile(r"is not recognized as the name of a cmdlet", re.I),
    re.compile(r"The term '.+?' is not recognized", re.I),
    re.compile(r"was unexpected at this time", re.I),
    re.compile(r"syntax error near unexpected token", re.I),
    re.compile(r"command not found", re.I),
    re.compile(r"MissingExpression|ParserError|Unexpected token", re.I),
    re.compile(r"no such file or directory", re.I),
)

_CIRCUIT_BREAKER_PREFIX = "CIRCUIT-BREAKER:"
# Tools whose repeated identical invocations are guarded.
_BREAKER_TOOLS = frozenset({"run_command", "run_task"})
# Tools whose success invalidates failure state (the command may now behave
# differently because the world changed).
_MUTATION_TOOLS = frozenset({
    "edit_file", "edit_lines", "create_file", "delete_file", "aider_edit",
})

_EXIT_CODE_RE = re.compile(r"[Ee]xit code:?\s*(-?\d+)")


def _normalize_command(command: str) -> str:
    return " ".join(command.split())


@dataclass
class FailureEntry:
    signature: str
    exit_code: int
    consecutive: int = 0
    mutation_epoch: int = 0
    intercepted: int = 0
    signature_line: str = ""

    @property
    def is_syntax(self) -> bool:
        return self.signature == "syntax"


class CommandFailureCache:
    """Tracks failed commands and intercepts wasteful identical retries.

    - Syntax failures: hard-intercept every identical repeat (deterministic
      failure — file edits cannot change command syntax).
    - Other failures: intercept the 3rd+ consecutive identical repeat when no
      mutation tool succeeded since the failure; every 3rd intercepted
      attempt still passes through (flaky-network escape valve).
    """

    def __init__(self) -> None:
        self._entries: dict[str, FailureEntry] = {}
        self._mutation_epoch: int = 0

    def note_mutation(self) -> None:
        self._mutation_epoch += 1

    @staticmethod
    def _classify(result: str) -> tuple[bool, str, int]:
        m = _EXIT_CODE_RE.search(result)
        exit_code = int(m.group(1)) if m else -1
        failed = exit_code != 0 or result.lstrip().startswith(("Error", "Command timed out"))
        if not failed:
            return False, "", exit_code
        for pat in _SYNTAX_FAILURE_PATTERNS:
            if pat.search(result):
                return True, "syntax", exit_code
        # Exit 127 (not found) / cmd exit 255 with parse markers.
        if exit_code in (127, 255):
            return True, "syntax", exit_code
        return True, "runtime", exit_code

    def check(self, command: str) -> str | None:
        """Return an interception message, or None to allow execution."""
        key = _normalize_command(command)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.is_syntax:
            entry.intercepted += 1
            # Pick the most informative line: skip section headers/exit lines.
            first_line = ""
            for line in entry.signature_line.splitlines():
                line = line.strip()
                if line and not line.startswith(("STDERR:", "STDOUT:", "Exit code")):
                    first_line = line[:200]
                    break
            return (
                f"{_CIRCUIT_BREAKER_PREFIX} This exact command already failed "
                f"with a shell syntax error (exit {entry.exit_code}): {first_line}\n"
                f"File edits cannot fix command syntax. Rewrite the command "
                f"(check path separators ./ vs .\\, redirects, quoting for the "
                f"active shell) — do NOT retry it unchanged."
            )
        # Runtime failure: allow the immediate retry (may be flaky), intercept
        # from the 3rd consecutive repeat onward — but only while no mutation
        # has happened since the failure (an edit invalidates it).
        if entry.mutation_epoch != self._mutation_epoch:
            return None
        if entry.consecutive < 2:
            return None
        entry.intercepted += 1
        if entry.intercepted % 3 == 0:
            return None  # escape valve: let every 3rd attempt actually run
        return (
            f"{_CIRCUIT_BREAKER_PREFIX} This exact command has now failed "
            f"{entry.consecutive} times in a row (last exit {entry.exit_code}) "
            f"with no intervening file changes. Re-running it unchanged cannot "
            f"produce a different result. Fix the underlying cause (edit the "
            f"code, install the dependency, change the arguments) or run a "
            f"DIFFERENT command to diagnose the failure."
        )

    def record(self, command: str, result: str) -> None:
        key = _normalize_command(command)
        failed, signature, exit_code = self._classify(result)
        if not failed:
            self._entries.pop(key, None)
            return
        entry = self._entries.get(key)
        if entry is None or entry.mutation_epoch != self._mutation_epoch:
            entry = FailureEntry(
                signature=signature,
                exit_code=exit_code,
                consecutive=0,
                mutation_epoch=self._mutation_epoch,
            )
            self._entries[key] = entry
        entry.consecutive += 1
        entry.exit_code = exit_code
        entry.signature = signature
        entry.signature_line = result.strip()[:300]
        # Bound the cache: keep only the most recent 64 distinct commands.
        if len(self._entries) > 64:
            for k in list(self._entries)[: len(self._entries) - 64]:
                del self._entries[k]


# --- Read-call deduplication ---------------------------------------------
#
# Read-only tools whose results are pure functions of the filesystem. A
# byte-identical repeat of one of these calls can only return a different
# result if a mutation tool succeeded in between — so an unchanged-world
# repeat is guaranteed waste and is hard-intercepted with a SYSTEM ERROR.
_DEDUP_PREFIX = "SYSTEM ERROR (dedup):"
_DEDUP_TOOLS = frozenset({
    "read_file", "grep_code", "glob_files", "list_dir", "find_symbols",
    "get_structure", "get_symbol_definition", "get_function_body",
    "find_references", "get_function_dependencies", "get_callers",
    "get_ast_range",
})

# Tools whose execution may mutate the filesystem through a shell (so the
# affected file paths cannot be read from the tool arguments). The tracker
# snapshots the working tree before/after these to detect ACTUAL changes —
# replacing the previous shell-command string parsing (which mis-detected
# arguments like 'gc.log' or '-3' as modified files).
_FS_SNAPSHOT_TOOLS = frozenset({
    "run_command", "run_task", "run_in_terminal", "shell_start",
})


class ReadDeduplicator:
    """Hard-intercepts exact-duplicate read-only tool calls.

    check() is consulted before execution; record() after. A repeat with an
    unchanged mutation epoch is intercepted ("you already ran this in call
    #N — check your memory"). A repeat after any successful mutation is
    allowed and refreshes the entry, since the result may have changed.
    Failed reads are never recorded, so retrying an error is always allowed.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple, tuple[int, int]] = {}  # key -> (call_index, epoch)
        self._call_counter = 0
        self._mutation_epoch = 0

    def note_mutation(self) -> None:
        self._mutation_epoch += 1

    @staticmethod
    def _key(name: str, arguments: dict[str, Any]) -> tuple:
        parts: list[tuple[str, Any]] = []
        for k, v in sorted(arguments.items()):
            if isinstance(v, str):
                parts.append((k, _normalize_command(v)))
            elif isinstance(v, (int, float, bool)):
                parts.append((k, v))
        return (name, tuple(parts))

    def check(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Return an interception message, or None to allow execution."""
        self._call_counter += 1
        key = self._key(name, arguments)
        entry = self._entries.get(key)
        if entry is None:
            return None
        first_call, epoch = entry
        if epoch != self._mutation_epoch:
            return None  # world changed since — allow; record() will refresh
        arg_summary = ", ".join(f"{k}={v!r}" for k, v in key[1][:4])
        if len(arg_summary) > 120:
            arg_summary = arg_summary[:117] + "..."
        return (
            f"{_DEDUP_PREFIX} You already ran {name}({arg_summary}) in call "
            f"#{first_call} and no files have been modified since — the "
            f"result would be byte-identical. Check your memory of that "
            f"output (it is in your context above). Do not repeat tool calls."
        )

    def record(self, name: str, arguments: dict[str, Any], result: str) -> None:
        if result.lstrip().startswith(("Error", _DEDUP_PREFIX)):
            return
        key = self._key(name, arguments)
        self._entries[key] = (self._call_counter, self._mutation_epoch)
        # Bound the cache: keep only the most recent 128 distinct calls.
        if len(self._entries) > 128:
            for k in list(self._entries)[: len(self._entries) - 128]:
                del self._entries[k]


# --- Identical failing-edit circuit breaker -------------------------------
#
# A byte-identical edit whose SEARCH text failed once will fail again unless
# the file changed — resubmitting it is guaranteed waste. The breaker allows
# one real retry (transient file locks happen) and intercepts from the 3rd.
_EDIT_BREAKER_TOOLS = frozenset({"edit_file", "edit_lines", "aider_edit", "create_file"})


class EditFailureBreaker:
    """Intercepts identical failing edits resubmitted unchanged.

    Tracks (tool, path, payload-hash) -> consecutive failure count. Any
    successful mutation (of any file) clears all state, since the mismatch
    may have been fixed by that edit.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple, tuple[int, str]] = {}  # key -> (consecutive, reason)
        self._mutation_epoch = 0

    def note_mutation(self) -> None:
        self._mutation_epoch += 1
        self._entries.clear()

    @staticmethod
    def _key(name: str, arguments: dict[str, Any]) -> tuple:
        path = str(arguments.get("path") or arguments.get("file_path") or "")
        payload = ""
        for k in ("search", "replace", "content"):
            v = arguments.get(k)
            if isinstance(v, str):
                digest = hashlib.sha256(v.encode("utf-8", "replace")).hexdigest()[:16]
                payload += f"{k}:{digest};"
        start = arguments.get("start", arguments.get("start_line"))
        end = arguments.get("end", arguments.get("end_line"))
        return (name, path.replace("\\", "/").lower(), payload, start, end)

    def check(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Return an interception message, or None to allow execution."""
        entry = self._entries.get(self._key(name, arguments))
        if entry is None:
            return None
        consecutive, reason = entry
        if consecutive < 2:
            return None
        if name == "create_file":
            return (
                f"{_CIRCUIT_BREAKER_PREFIX} This exact create_file call already "
                f"failed {consecutive} times ({reason}). The file already "
                f"exists and create_file will NEVER overwrite it — resubmitting "
                f"is guaranteed waste. If the existing content is already what "
                f"you want, move on. Otherwise read_file the current content "
                f"and use edit_file or edit_lines to change it."
            )
        return (
            f"{_CIRCUIT_BREAKER_PREFIX} This exact edit already failed "
            f"{consecutive} times ({reason}). The SEARCH text does not match "
            f"the current file contents, and no files have changed since. "
            f"Re-read the target region with get_function_body or read_file, "
            f"rebuild your SEARCH text from the ACTUAL current code, and "
            f"only then retry — do NOT resubmit the same edit unchanged."
        )

    def record(self, name: str, arguments: dict[str, Any], result: str) -> None:
        key = self._key(name, arguments)
        failed = result.lstrip().startswith(("Error", "Edit failed", "FAIL:"))
        if not failed:
            self._entries.pop(key, None)
            return
        reason = result.strip().splitlines()[0][:150] if result.strip() else "unknown"
        consecutive, _ = self._entries.get(key, (0, ""))
        self._entries[key] = (consecutive + 1, reason)
        if len(self._entries) > 64:
            for k in list(self._entries)[: len(self._entries) - 64]:
                del self._entries[k]


# --- Generic identical-error circuit breaker --------------------------------
#
# Commands and edits have their own specialised breakers above; this one
# covers EVERY OTHER tool. A byte-identical call that already returned the
# same error twice in a row cannot succeed on the third try — the world has
# not changed. The transcript that motivated this: shell_stop('bg_3')
# returning "not found" was re-issued six consecutive times while the
# orphaned server kept its port.
class IdenticalErrorBreaker:
    """Intercepts byte-identical calls that keep returning the same error.

    Applies to tools not covered by CommandFailureCache / EditFailureBreaker
    (shell_stop, shell_poll, fetch_url, nav tools, ...). Failures are only
    counted consecutively for the SAME (tool, args) key; a different call or
    a successful result resets the streak. Any successful mutation clears
    all state, since the world may have changed.
    """

    _ERROR_PREFIXES = ("Error", "ERROR", "FAIL:")

    def __init__(self) -> None:
        self._entries: dict[tuple, tuple[int, str]] = {}  # key -> (consecutive, first_line)

    def note_mutation(self) -> None:
        self._entries.clear()

    @staticmethod
    def _key(name: str, arguments: dict[str, Any]) -> tuple:
        parts: list[tuple[str, Any]] = []
        for k, v in sorted(arguments.items()):
            if isinstance(v, str):
                parts.append((k, _normalize_command(v)[:200]))
            elif isinstance(v, (int, float, bool)):
                parts.append((k, v))
        return (name, tuple(parts))

    @classmethod
    def _is_error(cls, result: str) -> bool:
        stripped = result.lstrip()
        return stripped.startswith(cls._ERROR_PREFIXES)

    def check(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Return an interception message, or None to allow execution."""
        entry = self._entries.get(self._key(name, arguments))
        if entry is None:
            return None
        consecutive, first_line = entry
        if consecutive < 2:
            return None
        return (
            f"{_CIRCUIT_BREAKER_PREFIX} This exact {name} call already failed "
            f"{consecutive} times with the same error: {first_line}\n"
            f"Retrying it unchanged cannot produce a different result. Change "
            f"the arguments, use a different tool, or address the cause in the "
            f"error message — do NOT repeat this call."
        )

    def record(self, name: str, arguments: dict[str, Any], result: str) -> None:
        key = self._key(name, arguments)
        # Circuit-breaker interceptions are GUIDANCE, not real failures —
        # leave the streak intact (do not count, do not reset) so an agent
        # that ignores the interception keeps getting intercepted.
        if result.startswith(_CIRCUIT_BREAKER_PREFIX):
            return
        if self._is_error(result):
            consecutive, first = self._entries.get(key, (0, ""))
            if not first:
                first = result.strip().splitlines()[0][:150] if result.strip() else "unknown"
            self._entries[key] = (consecutive + 1, first)
        else:
            self._entries.pop(key, None)
        # Bound the cache: keep only the most recent 64 distinct calls.
        if len(self._entries) > 64:
            for k in list(self._entries)[: len(self._entries) - 64]:
                del self._entries[k]


# Tools with their own specialised breakers — excluded from the generic one
# to keep failure accounting in exactly one place.
_GENERIC_BREAKER_EXCLUDED = _BREAKER_TOOLS | _EDIT_BREAKER_TOOLS


# --- Component scope guard --------------------------------------------------
#
# Armed by the Agent when the user's task explicitly scopes work to a named
# component ("the disassembler engine"). Forces optimization of the named
# component's internals instead of wrapping/caching around it.
_SCOPE_PREFIX = "SCOPE-GUARD:"


class ScopeGuard:
    """Restricts mutation tools to a user-named component's files.

    Modes (``scope_guard_mode`` in TierConfig):
      - ``"warn"`` (default): outside-scope edits execute, but the result is
        prefixed with a warning — fail-visible, never silently blocks.
      - ``"block"``: outside-scope edits are denied with an explanation.
      - ``"off"``: no-op.

    If arming resolved zero files the Agent leaves the guard disarmed, so it
    never blocks on a bad guess.
    """

    def __init__(self) -> None:
        self.mode = "warn"
        self._allowed: set[str] = set()
        self._dirs: set[str] = set()
        self._label = ""

    @property
    def armed(self) -> bool:
        return bool(self._allowed or self._dirs) and self.mode != "off"

    @property
    def label(self) -> str:
        return self._label

    def allowed_files(self) -> list[str]:
        return sorted(self._allowed)

    def arm(self, allowed: Iterable[str], label: str, dirs: Iterable[str] = ()) -> None:
        self._allowed = {self._normalize(p) for p in allowed if p}
        self._dirs = {self._normalize(d).rstrip("/") for d in dirs if d}
        self._label = label

    def disarm(self) -> None:
        self._allowed = set()
        self._dirs = set()
        self._label = ""

    @staticmethod
    def _normalize(path: str) -> str:
        return path.replace("\\", "/").lstrip("./").lower()

    @staticmethod
    def _extract_path(name: str, arguments: dict[str, Any]) -> str:
        if name == "aider_edit":
            content = arguments.get("content", "")
            if isinstance(content, str):
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        return line
            return ""
        path = arguments.get("path") or arguments.get("file_path") or ""
        return str(path)

    def _in_scope(self, norm_path: str) -> bool:
        if norm_path in self._allowed:
            return True
        # Files under an explicitly allowed directory are in scope (covers
        # directory-armed guards and new helper files created inside the
        # component).
        for d in self._dirs:
            if norm_path == d or norm_path.startswith(d + "/"):
                return True
        return False

    def check(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Block mode: return a denial for outside-scope edits, else None."""
        if not self.armed or self.mode != "block":
            return None
        raw = self._extract_path(name, arguments)
        if not raw:
            return None
        if self._in_scope(self._normalize(raw)):
            return None
        files = ", ".join(sorted(self._allowed)[:8])
        return (
            f"{_SCOPE_PREFIX} The task is scoped to the '{self._label}' "
            f"component ({len(self._allowed)} files: {files}). Editing "
            f"'{raw}' is outside that scope. Fix the problem INSIDE the "
            f"component — its algorithm, its data structures — rather than "
            f"wrapping, caching around, or patching other files to "
            f"compensate for it. If the scope is genuinely wrong, say so "
            f"explicitly in your reply instead of editing outside it."
        )

    def warn_if_outside(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Warn mode: return a warning prefix for outside-scope edits."""
        if not self.armed or self.mode != "warn":
            return None
        raw = self._extract_path(name, arguments)
        if not raw:
            return None
        if self._in_scope(self._normalize(raw)):
            return None
        return (
            f"{_SCOPE_PREFIX} WARNING: '{raw}' is outside the scoped "
            f"'{self._label}' component. The edit was applied, but the user "
            f"asked for changes inside that component — prefer fixing the "
            f"component's internals unless this file was strictly necessary.\n\n"
        )


_COMMON_ARG_FIXES: dict[str, dict[str, str]] = {
    "create_file": {
        "file": "path",
        "filename": "path",
        "filepath": "path",
        "data": "content",
        "text": "content",
        "body": "content",
        "code": "content",
        "source": "content",
    },
    "read_file": {
        "file": "path",
        "filename": "path",
        "filepath": "path",
    },
    "delete_file": {
        "file": "path",
        "filename": "path",
        "filepath": "path",
    },
    "edit_file": {
        "file": "path",
        "filename": "path",
        "filepath": "path",
        "old": "search",
        "new": "replace",
        "from": "search",
        "to": "replace",
    },
    "edit_lines": {
        "file": "path",
        "filename": "path",
        "filepath": "path",
        "new_content": "content",
    },
}

_CORRECT_USAGE_EXAMPLES: dict[str, str] = {
    "create_file": (
        'create_file(path="src/example.py", content="print(\'hello\')")'
    ),
    "edit_file": (
        'edit_file(path="src/example.py", search="old_text", replace="new_text")'
    ),
    "edit_lines": (
        'edit_lines(path="src/example.py", start=10, end=20, content="new lines")'
    ),
    "read_file": (
        'read_file(path="src/example.py")'
    ),
    "delete_file": (
        'delete_file(path="src/example.py")'
    ),
    "glob_files": (
        'glob_files(pattern="**/*.py")'
    ),
    "list_dir": (
        'list_dir(path="src")'
    ),
    "run_command": (
        'run_command(command="python test.py")'
    ),
    "run_in_terminal": (
        'run_in_terminal(command="python app.py", wait_seconds=5)'
    ),
    "grep_code": (
        'grep_code(pattern="def foo", path="src")'
    ),
    "find_symbols": (
        'find_symbols(query="ClassName")'
    ),
    "get_structure": (
        'get_structure(path="src")'
    ),
    "aider_edit": (
        'aider_edit(content="path/to/file.py\\n<<<<<<< SEARCH\\nold code\\n=======\\nnew code\\n>>>>>>> REPLACE")'
    ),
}


def _format_missing_arg_help(tool_name: str, schema: dict) -> str:
    """Generate a helpful message about missing required arguments."""
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    if not required:
        return ""

    parts = [f"The '{tool_name}' tool requires these arguments: {', '.join(required)}"]

    for arg in required:
        prop = properties.get(arg, {})
        arg_type = prop.get("type", "any")
        desc = prop.get("description", "")
        if desc:
            parts.append(f"  - {arg} ({arg_type}): {desc}")
        else:
            parts.append(f"  - {arg} ({arg_type})")

    # Add correct usage example
    example = _CORRECT_USAGE_EXAMPLES.get(tool_name)
    if example:
        parts.append(f"\nCorrect usage example:\n  {example}")

    return "\n".join(parts)


def _auto_repair_arguments(tool_name: str, arguments: dict[str, Any], schema: dict) -> dict[str, Any]:
    """Attempt to repair common malformed argument patterns.

    Handles cases where the LLM uses wrong key names, wraps args in extra structure,
    or passes arguments as positional values.
    """
    if not isinstance(arguments, dict):
        return arguments

    repaired = {}
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})

    # Quick check: if all required keys are present with non-None values, no repair needed
    if _has_all_required(required, arguments):
        # Still clean up: remove None values
        return {k: v for k, v in arguments.items() if v is not None}

    # Stage 1: Rename known aliases
    fixes = _COMMON_ARG_FIXES.get(tool_name, {})
    for alias_key, correct_key in fixes.items():
        if alias_key in arguments and correct_key not in arguments:
            # Only rename the alias, but keep original if present
            repaired[correct_key] = arguments[alias_key]

    # Stage 2: Handle nested structures (LLMs sometimes wrap args in "arguments" or "params")
    for nested_key in ("arguments", "params", "parameters", "args", "kwargs"):
        if nested_key in arguments and isinstance(arguments[nested_key], dict):
            nested = arguments[nested_key].copy()
            # Merge nested values that fill missing required keys
            for key in required:
                if key not in arguments and key in nested:
                    repaired[key] = nested[key]
            break  # Only follow one level of nesting

    # Stage 3: Handle the case where arguments is empty but there's a positional value
    # Some LLMs send {"arguments": "..."} where "..." is the single argument for aider_edit
    if tool_name == "aider_edit" and "arguments" in arguments:
        content_val = arguments.get("arguments")
        if content_val and isinstance(content_val, str) and "content" not in arguments:
            repaired["content"] = content_val

    # Stage 4: Strip None/empty string values for required args (they should be omitted)
    for key in list(arguments.keys()):
        val = arguments[key]
        if val is None or (isinstance(val, str) and val.strip() == ""):
            if key in required:
                pass  # Don't copy empty required vals, let validation catch them
            else:
                repaired[key] = arguments[key]  # Still copy non-required empty vals

    # Merge original arguments first, then repaired overrides
    merged = {**arguments, **repaired}

    # Remove any keys that are None to avoid confusing messages
    cleaned = {k: v for k, v in merged.items() if v is not None}

    return cleaned


def _has_all_required(required: set[str], args: dict) -> bool:
    """Check if all required arguments are present with non-None, non-empty values."""
    for key in required:
        if key not in args:
            return False
        val = args[key]
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return False
    return True


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.repo_path: Any = None
        # Optional approval gate consulted before destructive tool calls.
        # Wired by the CLI entry points (default/serve/task handlers); left
        # as a disabled no-op when the Agent is used as a library.
        self.gate: ApprovalGate | None = None
        # Circuit breaker for repeated identical failing commands. Enabled/
        # disabled via TierConfig.command_circuit_breaker_enabled (wired by
        # the Agent after construction).
        self.failure_cache = CommandFailureCache()
        self.circuit_breaker_enabled: bool = True
        # Hard dedup of exact-duplicate read-only calls (mutation-epoch
        # invalidated). TierConfig.read_dedup_enabled.
        self.read_dedup = ReadDeduplicator()
        self.read_dedup_enabled: bool = True
        # Circuit breaker for identical failing edits resubmitted unchanged.
        # TierConfig.edit_failure_breaker_enabled.
        self.edit_breaker = EditFailureBreaker()
        self.edit_failure_breaker_enabled: bool = True
        # Generic breaker: byte-identical calls to any other tool that keep
        # returning the same error (e.g. shell_stop on an unknown pid).
        # Gated by circuit_breaker_enabled like CommandFailureCache.
        self.generic_breaker = IdenticalErrorBreaker()
        # Component scope guard (armed by the Agent when the task names a
        # specific component). TierConfig.scope_guard_mode sets .mode.
        self.scope_guard = ScopeGuard()
        # Semantic filesystem state tracker (set by the Agent). When present,
        # mutating shell tools are snapshotted before/after execution to detect
        # real byte-level changes instead of parsing command strings. None in
        # library/test usage where the Agent is not fully constructed.
        self.fs_tracker: Any = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        if self.repo_path is None and getattr(tool, "repo_path", None) is not None:
            self.repo_path = tool.repo_path

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_schemas(self, names: list[str] | None = None) -> list[dict]:
        if names is None:
            return [t.to_openai_schema() for t in self._tools.values()]
        result = []
        for name in names:
            tool = self._tools.get(name)
            if tool:
                result.append(tool.to_openai_schema())
        return result

    @staticmethod
    def _validate_args(tool: Tool, arguments: dict[str, Any]) -> list[str]:
        errors = []
        schema = tool.schema or {}
        if not isinstance(arguments, dict):
            errors.append(f"Expected object arguments, got {type(arguments).__name__}")
            return errors

        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        missing = required - set(arguments.keys())
        for key in sorted(missing):
            errors.append(f"Missing required argument '{key}'")

        for key, value in arguments.items():
            prop = properties.get(key)
            if prop is None:
                continue
            expected_type = prop.get("type")
            if expected_type is None:
                continue
            type_map = {
                "string": str,
                "integer": int,
                "number": (int, float),
                "boolean": bool,
                "array": list,
                "object": dict,
            }
            py_type = type_map.get(expected_type)
            if py_type is None:
                continue
            if expected_type == "integer" and isinstance(value, bool):
                errors.append(f"Argument '{key}' must be an integer, got boolean")
            elif not isinstance(value, py_type):
                errors.append(
                    f"Argument '{key}' must be of type {expected_type}, got {type(value).__name__}"
                )

        # Check for None/empty string values on required args (LLM sometimes sends null)
        for key in required:
            if key in arguments and arguments[key] is None:
                missing.append(key)  # Treat None as missing

        return errors

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self._tools.keys())}"

        # --- Auto-repair common argument issues ---
        repaired = _auto_repair_arguments(name, arguments, tool.schema or {})
        if repaired != arguments:
            logger.debug("Repaired arguments for %s: %s -> %s", name, arguments, repaired)
            arguments = repaired

        validation_errors = self._validate_args(tool, arguments)
        if validation_errors:
            help_text = _format_missing_arg_help(name, tool.schema or {})
            return (
                f"Error: invalid arguments for tool '{name}':\n"
                + "\n".join(f"  - {e}" for e in validation_errors)
                + f"\n\n{help_text}"
                + f"\n\nExpected schema: {json.dumps(tool.schema, indent=2)}"
            )

        try:
            # --- CLI-only destructive-command approval gate ---
            # When armed by a CLI entry point, ask the user before running
            # git-revert / db-mutation commands. Returns a denial string the
            # tool loop surfaces to the model just like any other error.
            gate = self.gate
            if gate is not None and gate.enabled:
                reason = classify_dangerous(name, arguments)
                if reason:
                    if not await gate.request(name, arguments, reason):
                        preview = preview_command(name, arguments)
                        return (
                            f"Error: tool '{name}' was DENIED by the user — "
                            f"operation not executed.\nReason: {reason}\n"
                            f"Command: {preview}\n"
                            "Ask the user or choose a non-destructive approach."
                        )
            # --- Circuit breaker: intercept identical failing commands ---
            # Checked BEFORE execution so a guaranteed-failure retry costs no
            # process spawn. Only byte-identical repeats are intercepted —
            # any variation passes through.
            if self.circuit_breaker_enabled and name in _BREAKER_TOOLS:
                command = arguments.get("command", "")
                if isinstance(command, str) and command.strip():
                    intercept = self.failure_cache.check(command)
                    if intercept is not None:
                        logger.info("circuit breaker intercepted %s: %.80s", name, command)
                        return intercept
            # --- Edit breaker: intercept identical failing edits ---
            if self.edit_failure_breaker_enabled and name in _EDIT_BREAKER_TOOLS:
                intercept = self.edit_breaker.check(name, arguments)
                if intercept is not None:
                    logger.info("edit breaker intercepted %s on %s", name, arguments.get("path", ""))
                    return intercept
            # --- Scope guard: block-mode denial for outside-scope edits ---
            if name in _MUTATION_TOOLS:
                denial = self.scope_guard.check(name, arguments)
                if denial is not None:
                    logger.info("scope guard denied %s on %s", name, arguments.get("path", ""))
                    return denial
            # --- Read dedup: intercept exact-duplicate read-only calls ---
            if self.read_dedup_enabled and name in _DEDUP_TOOLS:
                intercept = self.read_dedup.check(name, arguments)
                if intercept is not None:
                    logger.info("read dedup intercepted %s", name)
                    return intercept
            # --- Generic breaker: intercept identical calls that keep ---
            # --- returning the same error (any tool not covered above) ---
            if self.circuit_breaker_enabled and name not in _GENERIC_BREAKER_EXCLUDED:
                intercept = self.generic_breaker.check(name, arguments)
                if intercept is not None:
                    logger.info("generic breaker intercepted %s", name)
                    return intercept
            # --- Filesystem snapshot before shell-mutating tools ---
            # The tracker detects ACTUAL byte-level changes (created/modified/
            # deleted) by diffing the working tree before/after, instead of
            # parsing the command string. Reuses the previous snapshot when it
            # is still fresh to halve the walk cost for back-to-back calls.
            tracker = self.fs_tracker
            fs_before = None
            if tracker is not None and name in _FS_SNAPSHOT_TOOLS:
                try:
                    fs_before = await asyncio.to_thread(tracker.snapshot_cached)
                except Exception:
                    fs_before = None
            result = await tool.run(**arguments)
            # --- Filesystem snapshot after + reconcile actual changes ---
            if fs_before is not None and tracker is not None:
                try:
                    fs_after = await asyncio.to_thread(tracker.snapshot)
                    fs_changes = tracker.diff(fs_before, fs_after)
                except Exception:
                    fs_changes = []
                if fs_changes:
                    # A shell write mutated real files — invalidate every
                    # breaker's mutation epoch so re-reads are allowed (fixes
                    # the "shell writes bypass change tracking" gap) and surface
                    # the actual changes to the agent's context.
                    tracker.last_changes = fs_changes
                    self.failure_cache.note_mutation()
                    self.read_dedup.note_mutation()
                    self.edit_breaker.note_mutation()
                    self.generic_breaker.note_mutation()
                    note = tracker.format_changes_note(fs_changes)
                    if note:
                        sep = "\n" if result and not result.endswith("\n") else ""
                        result = f"{result}{sep}{note}"
                    tracker.verify_async(fs_changes)
            # --- Post-execution: feed the breakers, dedup, and mutation epoch ---
            if self.edit_failure_breaker_enabled and name in _EDIT_BREAKER_TOOLS:
                self.edit_breaker.record(name, arguments, result)
            if name in _MUTATION_TOOLS:
                if not result.lstrip().startswith(("Error", "Edit failed", "FAIL:")):
                    self.failure_cache.note_mutation()
                    self.read_dedup.note_mutation()
                    self.edit_breaker.note_mutation()
                    self.generic_breaker.note_mutation()
                    warn = self.scope_guard.warn_if_outside(name, arguments)
                    if warn:
                        result = warn + result
            elif self.circuit_breaker_enabled and name in _BREAKER_TOOLS:
                command = arguments.get("command", "")
                if isinstance(command, str) and command.strip():
                    self.failure_cache.record(command, result)
            if self.read_dedup_enabled and name in _DEDUP_TOOLS:
                self.read_dedup.record(name, arguments, result)
            if self.circuit_breaker_enabled and name not in _GENERIC_BREAKER_EXCLUDED:
                self.generic_breaker.record(name, arguments, result)
            return result
        except TypeError as e:
            if "missing" in str(e) and "required positional argument" in str(e):
                return f"Error executing {name}: {e}. This usually means the LLM sent empty or malformed arguments."
            return f"Error executing {name}: {e}"
        except Exception as e:
            return f"Error executing {name}: {e}"

    async def safe_execute(self, name: str, arguments: dict[str, Any], max_retries: int = 2, base_delay: float = 0.5) -> str:
        last_error = ""
        for attempt in range(max_retries):
            if attempt > 0:
                delay = base_delay * (2 ** attempt)
                logger.debug("safe_execute: retry %d/%d for %s in %.1fs", attempt + 1, max_retries, name, delay)
                await asyncio.sleep(delay)

            result = await self.execute(name, arguments)
            # Retry on BOTH execution errors and validation errors
            if result.startswith("Error executing ") or result.startswith("Error: invalid arguments for tool "):
                last_error = result
                continue  # retry
            return result  # success or other non-retryable error

        if last_error:
            return f"ERROR after {max_retries} retries: {last_error}"
        return result

    def tool_descriptions(self) -> str:
        lines = []
        for t in self._tools.values():
            params = ", ".join(
                f"{pname}: {pinfo.get('type', 'any')}"
                for pname, pinfo in t.schema.get("properties", {}).items()
            )
            lines.append(f"- {t.name}({params}): {t.description}")
        return "\n".join(lines)
