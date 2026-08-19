from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_READ_TOOLS = frozenset({
    "read_file", "grep_code", "find_symbols", "get_structure",
    "glob_files", "list_dir",
    "get_function_body", "find_references", "get_symbol_definition",
    "get_function_dependencies", "get_callers", "get_ast_range",
})
_WRITE_TOOLS = frozenset({
    "edit_file", "edit_lines", "create_file", "delete_file", "aider_edit",
})
# run_command is read-ish when it uses a read utility and no write redirect.
_READ_CMDS = (
    "cat", "head", "tail", "wc", "less", "more", "grep", "find", "ls",
    "sed", "rg", "awk", "nl", "bat", "echo", "tree",
)
_WRITE_CMD_MARKERS = (">", ">>", "| tee", "sponge", "cat >", "echo >", "write(")


def _command_is_read_only(command: str) -> bool:
    """Heuristic: does a shell command only read (not write) files?"""
    if not command:
        return False
    if any(m in command for m in _WRITE_CMD_MARKERS):
        return False
    stripped = command.strip()
    # Strip leading env assignments / sudo / time wrappers.
    for prefix in ("sudo ", "time ", "nice ", "command "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
    return any(stripped.startswith(c) for c in _READ_CMDS)


@dataclass(frozen=True)
class ToolFingerprint:
    name: str
    key_args: tuple[tuple[str, Any], ...]

    @classmethod
    def from_call(cls, name: str, arguments: dict[str, Any]) -> ToolFingerprint:
        # Must include every argument that distinguishes one call from another.
        # Omitting a tool's key argument (e.g. web_search's `query`) makes all
        # its calls fingerprint identically, and productive iteration then
        # trips the identical-turns kill switch.
        keys = (
            "path", "file_path", "cwd", "pattern", "command",
            "start_line", "end_line", "start", "end",
            "query", "url", "library", "topic", "version", "name", "symbol",
            # Background-job id: without it, shell_poll/shell_stop/shell_input
            # on DIFFERENT jobs fingerprint identically and look like repeats,
            # while the genuinely identical retry loop (shell_stop('bg_3') x6)
            # is what the identical-turns kill switch exists to catch.
            "pid",
        )
        # Content-bearing args that distinguish otherwise-identical calls.
        # Two edit_file calls on the same file with different search/replace
        # text are NOT the same call — without including these, every edit to
        # the same path collides into one fingerprint and triggers false
        # "exact same tool calls" critical stops. Long values are hashed so
        # the fingerprint stays compact while remaining distinguishing.
        # Applied per-tool so a stray arg on an unrelated tool (e.g. a
        # read_file with a bogus `content` key) is still ignored.
        content_keys_by_tool: dict[str, tuple[str, ...]] = {
            "edit_file": ("search", "replace"),
            "edit_lines": ("content",),
            "aider_edit": ("content",),
            "create_file": ("content",),
        }
        key_args = []
        for k in keys:
            if k in arguments:
                v = arguments[k]
                if isinstance(v, str):
                    key_args.append((k, v))
                elif isinstance(v, (int, float)):
                    key_args.append((k, str(v)))
        for k in content_keys_by_tool.get(name, ()):
            if k in arguments and isinstance(arguments[k], str):
                v = arguments[k]
                if len(v) > 64:
                    import hashlib
                    key_args.append((k, f"hash:{hashlib.sha256(v.encode('utf-8', 'replace')).hexdigest()[:16]}"))
                else:
                    key_args.append((k, v))
        for k in ("replaceAll",):
            if k in arguments:
                key_args.append((k, arguments[k]))
        return cls(name, tuple(sorted(key_args)))


@dataclass
class TurnRecord:
    fingerprints: frozenset[ToolFingerprint]
    files_read: frozenset[str] = field(default_factory=frozenset)
    files_modified: frozenset[str] = field(default_factory=frozenset)


@dataclass
class LoopCheck:
    severity: str  # "ok", "warning", or "critical"
    reason: str
    repeated_count: int = 0
    turns_without_progress: int = 0
    read_only_repeat_count: int = 0


class LoopDetector:
    def __init__(
        self,
        window_size: int = 8,
        max_repetitions: int = 3,
        stagnation_threshold: int = 6,
        read_only_warn_turns: int = 6,
        same_file_reread_warn: int = 4,
        consecutive_chunk_warning: int = 3,
        consecutive_chunk_critical: int = 5,
        identical_turns_critical: int = 5,
    ):
        self.window_size = window_size
        self.max_repetitions = max_repetitions
        self.stagnation_threshold = stagnation_threshold
        self.read_only_warn_turns = read_only_warn_turns
        self.same_file_reread_warn = same_file_reread_warn
        self.consecutive_chunk_warning = consecutive_chunk_warning
        self.consecutive_chunk_critical = consecutive_chunk_critical
        self.identical_turns_critical = identical_turns_critical
        self._history: list[TurnRecord] = []
        self._cumulative_files_read: set[str] = set()
        self._cumulative_files_modified: set[str] = set()
        self._turns_without_progress = 0
        self._read_only_repeat_count = 0
        self._last_turn_was_read_only = False

        # Track (path, start, end) tuples to distinguish
        # "reading new chunks" from "re-reading the same exact content"
        self._read_chunks: set[tuple[str, int | None, int | None]] = set()
        self._consecutive_identical_chunks = 0
        self._last_chunk_key: tuple[str, int | None, int | None] | None = None

        # Track exact-identical consecutive TURNS (the whole tool-call set).
        # This is the ONLY condition that escalates to a hard "critical" stop:
        # the agent is emitting byte-identical calls over and over, which is a
        # genuine infinite loop. Everything else is a soft warning.
        self._consecutive_identical_turns = 0
        self._last_fingerprints: frozenset[ToolFingerprint] | None = None

    def _chunk_key(self, call: Any) -> tuple[str, int | None, int | None] | None:
        """Extract (path, start, end) from a read_file call if present.

        NOTE: read_file's schema uses ``start``/``end`` (not ``start_line``/
        ``end_line``), so we read those keys. Without this, every read of the
        same file collides into the same (path, None, None) key and any
        sequence of reads of different line ranges is misclassified as
        re-reading the "same chunk".
        """
        if call.name != "read_file":
            return None
        path = call.arguments.get("path") or call.arguments.get("file_path", "")
        start = call.arguments.get("start", call.arguments.get("start_line"))
        end = call.arguments.get("end", call.arguments.get("end_line"))
        return (str(path), start, end)

    def record(
        self,
        tool_calls: list[Any],
        files_read: list[str],
        files_modified: list[str],
    ) -> LoopCheck:
        fingerprints = frozenset(
            ToolFingerprint.from_call(tc.name, tc.arguments)
            for tc in tool_calls
        )
        record = TurnRecord(
            fingerprints=fingerprints,
            files_read=frozenset(files_read),
            files_modified=frozenset(files_modified),
        )

        repetition_count = 0
        for past in self._history:
            if past.fingerprints == fingerprints:
                repetition_count += 1

        # Track read-only cycles and same-file re-reads.
        # Derive "read-only turn" from the actual tool calls, not just from
        # path extraction (which is unreliable for shell grep/sed/rg). A turn
        # is read-only if it used a read/search tool or a read-ish shell
        # command, and did not use any write/edit tool.
        has_read_tool = False
        has_write_tool = False
        for call in tool_calls:
            name = getattr(call, "name", "")
            if name in _WRITE_TOOLS:
                has_write_tool = True
            elif name in _READ_TOOLS:
                has_read_tool = True
            elif name == "run_command":
                cmd = (call.arguments.get("command", "") if hasattr(call, "arguments") else "") or ""
                if _command_is_read_only(cmd):
                    has_read_tool = True
        is_read_only_turn = has_read_tool and not has_write_tool
        if is_read_only_turn and self._last_turn_was_read_only:
            self._read_only_repeat_count += 1
        elif not is_read_only_turn:
            self._read_only_repeat_count = 0
        self._last_turn_was_read_only = is_read_only_turn

        # Track how many times we re-read files we've already seen
        same_file_rereads = 0
        if is_read_only_turn:
            for fpath in files_read:
                if fpath in self._cumulative_files_read:
                    same_file_rereads += 1

        # Track exact same read_file chunk repeats (path + offsets identical)
        # This detects the "truncated output → re-read same chunk" loop pattern
        for call in tool_calls:
            ck = self._chunk_key(call)
            if ck:
                if ck in self._read_chunks:
                    # Already read this exact chunk before, but not necessarily consecutive
                    pass
                self._read_chunks.add(ck)
                if ck == self._last_chunk_key:
                    self._consecutive_identical_chunks += 1
                else:
                    self._consecutive_identical_chunks = 0
                self._last_chunk_key = ck

        # --- Exact-identical consecutive TURNS: the ONLY critical stop ---
        # Per policy, the loop is never killed for exploration/re-reading; we
        # trust the agent to know what it is doing. The single exception is a
        # genuine infinite loop: the EXACT same tool-call set emitted
        # repeatedly with zero variation. Even then we give the agent a wide
        # berth (identical_turns_critical, default 5) before intervening.
        if self._last_fingerprints is not None and fingerprints == self._last_fingerprints:
            self._consecutive_identical_turns += 1
        else:
            self._consecutive_identical_turns = 0
        self._last_fingerprints = fingerprints

        if self._consecutive_identical_turns >= self.identical_turns_critical:
            return LoopCheck(
                severity="critical",
                reason=(
                    f"The exact same tool calls have been emitted "
                    f"{self._consecutive_identical_turns + 1} consecutive times "
                    f"with no variation. This is a genuine infinite loop — "
                    f"stopping to prevent a hang."
                ),
            )

        # Count how many different files we've explored this session
        unique_files_explored = len(self._cumulative_files_read)

        # --- Read-only exploration: NEVER critical ---
        # Exploration is productive work. Give helpful hints at moderate thresholds
        # but never kill the loop for pure exploration. Thresholds are configurable
        # via the LoopDetector constructor (read_only_warn_turns, same_file_reread_warn).
        if self._read_only_repeat_count >= self.read_only_warn_turns or same_file_rereads >= self.same_file_reread_warn:
            return LoopCheck(
                severity="warning",
                reason=(
                    f"You've had {self._read_only_repeat_count} consecutive read-only turns "
                    f"({same_file_rereads} same-file re-reads, "
                    f"{unique_files_explored} unique files explored so far). "
                    f"You already have enough context about this file. STOP re-reading "
                    f"overlapping regions and either make the edit or, if you need a "
                    f"specific line, run `wc -l <file>` once to get the length. "
                    f"If tool output keeps getting truncated, read a narrower line range."
                ),
                read_only_repeat_count=self._read_only_repeat_count,
            )

        # --- Same exact read_file chunk repeated consecutively ---
        # WARNING ONLY. Re-reading the same chunk may be the agent deliberately
        # re-checking state after edits; we never hard-stop exploration. We just
        # nudge the model toward a different approach once it clearly is not
        # making progress. The only hard stop is the exact-identical-turns
        # check above.
        if self._consecutive_identical_chunks >= self.consecutive_chunk_warning:
            return LoopCheck(
                severity="warning",
                reason=(
                    f"You've re-read the exact same file chunk "
                    f"{self._consecutive_identical_chunks + 1} consecutive times. "
                    f"The output is not changing. Use a completely different approach: "
                    f"try `wc -l <file>` to check length, or `grep` for specific patterns. "
                    f"If you already have enough context, STOP reading and produce your answer."
                ),
            )

        new_files = (
            (record.files_read - self._cumulative_files_read)
            | (record.files_modified - self._cumulative_files_modified)
        )
        if new_files or record.files_modified:
            self._turns_without_progress = 0
        else:
            self._turns_without_progress += 1

        self._cumulative_files_read |= record.files_read
        self._cumulative_files_modified |= record.files_modified

        self._history.append(record)
        if len(self._history) > self.window_size:
            self._history.pop(0)

        # Exact same tool call pattern repeated — this is a warning, not critical.
        # Consecutive command/shell calls are NOT considered stuck behavior.
        if repetition_count >= self.max_repetitions:
            return LoopCheck(
                severity="warning",
                reason=(
                    f"Exact same tool call pattern repeated {repetition_count} "
                    f"times in the last {len(self._history)} turns. "
                    f"If this keeps failing, try a different approach."
                ),
                repeated_count=repetition_count,
                turns_without_progress=self._turns_without_progress,
            )

        if repetition_count >= max(1, self.max_repetitions - 1):
            return LoopCheck(
                severity="warning",
                reason=(
                    f"You just repeated a very similar tool call. "
                    f"If this fails again, change your strategy instead of retrying."
                ),
                repeated_count=repetition_count,
                turns_without_progress=self._turns_without_progress,
            )

        # Stagnation detection is removed: calling consecutive commands is
        # productive work, not being stuck. Only exact repetition matters.
        return LoopCheck(
            severity="ok",
            reason="",
            repeated_count=repetition_count,
            turns_without_progress=self._turns_without_progress,
        )

    def reset(self) -> None:
        self._history.clear()
        self._cumulative_files_read.clear()
        self._cumulative_files_modified.clear()
        self._turns_without_progress = 0
        self._read_chunks.clear()
        self._consecutive_identical_chunks = 0
        self._last_chunk_key = None
        self._consecutive_identical_turns = 0
        self._last_fingerprints = None

    def progress_summary(self) -> str:
        lines = []
        if self._cumulative_files_read:
            lines.append(f"Files read ({len(self._cumulative_files_read)}): {', '.join(sorted(self._cumulative_files_read)[:10])}")
            if len(self._cumulative_files_read) > 10:
                lines.append(f"  ... and {len(self._cumulative_files_read) - 10} more")
        if self._cumulative_files_modified:
            lines.append(f"Files modified ({len(self._cumulative_files_modified)}): {', '.join(sorted(self._cumulative_files_modified))}")
        if not lines:
            lines.append("No files read or modified yet.")
        return "\n".join(lines)
