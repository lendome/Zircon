"""Tool loop executor with streaming support."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .types import StreamChunk, TraceEvent, TierConfig, ToolCall, CompletionDisposition
from .loop_detector import LoopDetector
from .syntax_integration import (
    check_file_after_edit,
    format_errors_for_loop,
    supports_immediate_syntax_check,
)
from ..tools.registry import ToolRegistry
from ..llm.router import ModelRouter
from .runtime_probe import RuntimeProbe, ProbeResult, extract_facts, is_local_url, normalize_probe_url
from .completion_gate import ExecutionState, evaluate_completion, classify_task
from .context_guard import guard_messages, truncate_oversized_content
from .context_window_guard import is_context_length_error

logger = logging.getLogger("agent.executor")


def _response_was_truncated(finish_reason: str) -> bool:
    """Return whether a provider stopped generation at its output limit."""
    return (finish_reason or "").lower() in {
        "length",
        "max_tokens",
        "max_output_tokens",
        "token_limit",
    }


_PREMATURE_COMPLETION_RE = re.compile(
    r"^(?:done|finished|complete(?:d)?|all\s+set|that(?:'s| is)\s+it|"
    r"task\s+(?:complete|completed|finished))(?:[.!\s]*)$",
    re.IGNORECASE,
)


def _is_premature_completion(text: str, state: ExecutionState, has_done_work: bool) -> bool:
    """Reject a bare completion marker before implementation work exists."""
    if has_done_work or not text.strip():
        return False
    if "implementation" not in classify_task(state):
        return False
    return bool(_PREMATURE_COMPLETION_RE.fullmatch(text.strip()))


def _short_args(args: dict, max_len: int = 60) -> str:
    """Compact one-line preview of tool arguments for progress labels."""
    parts: list[str] = []
    for k, v in (args or {}).items():
        s = str(v)
        if len(s) > max_len:
            s = s[:max_len] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:120]


@dataclass
class ExecutionResult:
    success: bool
    output: str
    tool_calls_made: int = 0
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    trace: list[TraceEvent] = field(default_factory=list)
    history_turns: list[dict] = field(default_factory=list)
    disposition: CompletionDisposition = CompletionDisposition.VERIFIED
    evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    state_facts: list[str] = field(default_factory=list)


class Executor:
    def __init__(self, router: ModelRouter, registry: ToolRegistry, tier_config: TierConfig | None = None, role: str = "default"):
        self.router = router
        self.registry = registry
        self.tier = tier_config or TierConfig(name="balanced")
        # Which model role this executor's LLM calls use. The main coding agent
        # uses "default"; the researcher uses "research" so it can be swapped
        # independently in models.yaml.
        self.role = role or "default"
        # Optional mid-loop advisor hook (Advisor-Agent pattern). Signature:
        # async (turn: int, task: str, trajectory_digest: str) -> str | None.
        # Wired by the Agent; None everywhere else (sub-agents, research, tests).
        self.advisor_callback: Any = None
        # Model id of the advisor (set by the Agent); included in check-in
        # events/chunks so the TUI can display which model is advising.
        self.advisor_model: str = ""
        self._last_history_turns: list[dict] = []
        # Active tool gates: tool name -> [turns_left, reason]. Gated tools are
        # stripped from the offered schemas each turn and denied in
        # _execute_batch. Written by the web-search anti-thrash logic and by
        # advisor vetoes; never contains read/navigation tools.
        self._tool_gates: dict[str, list] = {}
        # Cooldown after a veto expires: tool name -> turns left before that
        # tool can be vetoed again (prevents perma-ban via repeated check-ins).
        self._veto_cooldown: dict[str, int] = {}
        self._loop_detector = LoopDetector(
            window_size=self.tier.loop_detection_window,
            max_repetitions=self.tier.loop_max_repetitions,
            stagnation_threshold=self.tier.loop_stagnation_threshold,
            read_only_warn_turns=self.tier.loop_read_only_warn_turns,
            same_file_reread_warn=self.tier.loop_same_file_reread_warn,
            consecutive_chunk_warning=self.tier.loop_consecutive_chunk_warning,
            consecutive_chunk_critical=self.tier.loop_consecutive_chunk_critical,
        )
        self._recovery_attempts = 0
        self._verification_ran = False
        self._test_nudge_used = False
        self._truncation_retries = 0
        self._budget_nudged = False
        self._last_loop_warning_reason: str = ""
        self._probe = RuntimeProbe()
        self._exec_state = ExecutionState()
        self._last_state_block: str = ""
        # Files already attributed from the snapshot tracker this run (avoids
        # re-listing the same cumulative change every turn).
        self._fs_seen: set[str] = set()
        from .trajectory_diet import TrajectoryPruner
        self._trajectory_pruner = TrajectoryPruner(
            tier_config=self.tier,
            context_window=getattr(self.tier, "context_window", getattr(router, "context_window", 32000)),
        )
        from .context_window_guard import ContextWindowGuard
        self._ctx_guard = ContextWindowGuard(
            tier_config=self.tier,
            context_window=getattr(self.tier, "context_window", getattr(router, "context_window", 128000)),
        )
        self._ctx_error_retries = 0
        self._llm_error_retries = 0
        self._forced_continuations = 0

    def reset_loop_detector(self) -> None:
        self._loop_detector.reset()

    def reset_recovery(self) -> None:
        self._recovery_attempts = 0
        self._verification_ran = False
        self._test_nudge_used = False
        self._truncation_retries = 0
        self._budget_nudged = False
        self._last_loop_warning_reason = ""
        self._probe.reset()
        self._exec_state = ExecutionState()
        self._last_state_block = ""
        self._fs_seen = set()
        tracker = getattr(self.registry, "fs_tracker", None)
        if tracker is not None:
            tracker.reset()
        self._searches_since_read = 0
        self._research_last_intervention = 0
        self._tool_gates = {}
        self._veto_cooldown = {}
        self._consecutive_tool_failures = {}
        self._trajectory_pruner.reset()
        self._ctx_guard.reset()
        self._ctx_error_retries = 0
        self._llm_error_retries = 0
        self._forced_continuations = 0
        self.reset_loop_detector()

    def _persist_guard_summary(self, history_turns: list[dict]) -> None:
        """Persist the in-loop compaction summary into durable history.

        The guard compacts the ACTIVE conversation; without this the summary
        would vanish with the loop and the next chat turn would resume with
        only distilled stubs — "completely out of context". Idempotent per
        summary text.
        """
        summary = getattr(self._ctx_guard, "last_summary", "")
        if not summary or summary == getattr(self, "_last_persisted_summary", ""):
            return
        self._last_persisted_summary = summary
        note = (
            "<history_summary>Earlier work in this session (compacted mid-task "
            "to fit the context window)</history_summary>\n" + summary
        )
        history_turns.append({"role": "system", "content": note})

    def _persist_guard_summary_stream(self) -> None:
        self._persist_guard_summary(self._last_history_turns)

    # --- Advisor check-in (mid-loop feedback) --------------------------------

    # Tools the advisor may veto (mutation/search only). Read and navigation
    # tools can never be gated — blinding the agent helps no one — and
    # run_command stays available as the verification lifeline.
    _VETOABLE_TOOLS = frozenset({
        "edit_file", "edit_lines", "create_file", "aider_edit",
        "delete_file", "web_search",
    })
    _VETO_BLOCK_RE = re.compile(
        r"```veto\s*\n(.*?)\n?```", re.DOTALL | re.IGNORECASE
    )
    _VETO_COOLDOWN_TURNS = 5

    @classmethod
    def _parse_veto(cls, feedback: str) -> tuple[str, dict | None]:
        """Extract a ```veto block from advisor feedback.

        Returns (cleaned_feedback, veto_dict|None). The block is stripped so
        the note the agent reads is plain prose; enforcement is reported
        separately.
        """
        match = cls._VETO_BLOCK_RE.search(feedback or "")
        if not match:
            return feedback, None
        body = match.group(1)
        veto: dict = {}
        for line in body.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            veto[key.strip().lower()] = value.strip()
        cleaned = (feedback[: match.start()] + feedback[match.end():]).strip()
        if "tool" not in veto:
            return cleaned, None
        try:
            veto["turns"] = int(veto.get("turns", "2"))
        except ValueError:
            veto["turns"] = 2
        return cleaned, veto

    def _apply_veto(self, veto: dict) -> str | None:
        """Validate and arm a tool gate from an advisor veto.

        Returns a short enforcement note for the agent, or None when the veto
        was rejected (non-whitelisted tool, cooldown, already gated, disabled).
        """
        if not self.tier.advisor_veto_enabled:
            return None
        tool = str(veto.get("tool", "")).strip()
        if tool not in self._VETOABLE_TOOLS:
            return None
        if tool in self._tool_gates:
            return None  # already gated — don't stack
        if self._veto_cooldown.get(tool, 0) > 0:
            return None
        turns = max(1, min(int(veto.get("turns", 2)), self.tier.advisor_veto_max_turns))
        reason = str(veto.get("reason", "")).strip() or "advisor-directed pause"
        self._tool_gates[tool] = [turns, reason]
        self._last_veto_note = f"{tool} disabled for {turns} turn(s) — {reason}"
        logger.info("advisor veto: %s gated for %d turns (%s)", tool, turns, reason)
        return f"ENFORCED: `{tool}` is disabled for the next {turns} turn(s) — {reason}."

    @staticmethod
    def _advisor_digest(history_turns: list[dict], max_chars: int = 3000) -> str:
        """Compact digest of recent loop activity for the advisor check-in.

        Keeps only the tail of the trajectory and truncates aggressively — the
        advisor needs the shape of the work, not full file contents.
        """
        lines: list[str] = []
        for msg in history_turns[-40:]:
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    args = (fn.get("arguments") or "").replace("\n", " ")
                    lines.append(f"-> {fn.get('name', '?')}({args[:100]})")
                content = (msg.get("content") or "").strip()
                if content:
                    lines.append(f"assistant: {content[:200]}")
            elif role == "tool":
                lines.append(f"result: {(msg.get('content') or '')[:200]}")
            elif role == "system":
                lines.append(f"system: {(msg.get('content') or '')[:200]}")
        return "\n".join(lines)[-max_chars:]

    async def _maybe_advisor_checkin(
        self,
        turn: int,
        history_turns: list[dict],
        current_messages: list[dict],
    ) -> str | None:
        """Run the mid-loop advisor check-in and inject its feedback note.

        Returns the feedback text when a note was injected, else None. Never
        raises — a failed advisor must not break the tool loop.
        """
        callback = self.advisor_callback
        interval = self.tier.advisor_checkin_interval
        if callback is None or interval <= 0 or turn % interval != 0:
            return None
        try:
            feedback = await callback(turn, self._exec_state.task, self._advisor_digest(history_turns))
        except Exception as e:
            logger.warning("Advisor check-in failed (%s), skipping", e)
            return None
        if not feedback:
            return None
        # A ```veto block in the feedback is an ENFORCEMENT directive, not
        # prose: parse it out, arm the tool gate, and report the enforcement
        # in the note so the agent understands why a tool disappeared.
        feedback, veto = self._parse_veto(feedback)
        enforcement = self._apply_veto(veto) if veto else None
        note = (
            f"<advisor_feedback turn=\"{turn}\">\n{feedback}\n</advisor_feedback>\n"
            "This is guidance from a senior advisor reviewing your work: keep doing "
            "what was approved, address the criticisms, and apply the must-fix items."
        )
        if enforcement:
            note += f"\n{enforcement}"
        current_messages.append({"role": "system", "content": note})
        history_turns.append({"role": "system", "content": note})
        return feedback

    # --- Execution-state tracking ------------------------------------------

    # Cap on total gate-forced continuations per run. Every anti-loop gate
    # (edit-mode force, empty-response force, evidence gate, explanation
    # gate) ends in `continue` — if the model keeps producing the same
    # non-compliant response, an UNBOUNDED gate deadlocks the tool loop
    # forever (e.g. the completion gate demands a build artifact for a task
    # that has no build step). Beyond the cap we accept the response and
    # report the outcome honestly.
    _MAX_FORCED_CONTINUATIONS = 6

    def _can_force_continuation(self) -> bool:
        """Bounded permission slip for gate-forced continuations (see above)."""
        if self._forced_continuations >= self._MAX_FORCED_CONTINUATIONS:
            return False
        self._forced_continuations += 1
        return True

    def _maybe_budget_nudge(self, turn: int, effective_max: int | None) -> str | None:
        """One-time pressure note when 80% of the tool-turn budget is used.

        Mirrors the subagent TURN BUDGET note (subagents/base.py): near the
        end of the budget the model must stop exploring and converge on an
        edit + verification. Fires at most once per run (reset_recovery).
        Only applicable when a caller explicitly passed a max_turns budget —
        the main agent loop has no turn budget.
        """
        if self._budget_nudged or not effective_max or effective_max <= 0:
            return None
        threshold = max(1, int(effective_max * 0.8))
        if turn < threshold:
            return None
        self._budget_nudged = True
        remaining = max(0, effective_max - turn)
        return (
            f"<system_note>\n"
            f"TURN BUDGET: {remaining} of {effective_max} tool turns remain. "
            f"Stop exploring — commit to your best hypothesis, make the edit, "
            f"and verify it with a build or test run now.\n"
            f"</system_note>"
        )

    def _state_block(self) -> str:
        """Compact, deterministic facts injected before every provider request."""
        # Reconcile actual filesystem mutations detected by the snapshot
        # tracker (shell writes, etc.) into the execution state so the model
        # sees real byte-level changes rather than shell-command-string guesses.
        tracker = getattr(self.registry, "fs_tracker", None)
        if tracker is not None:
            self._exec_state.files_modified.update(tracker.changed_files())
        facts = self._exec_state.facts_for_prompt()
        if not facts:
            return ""
        body = "\n".join(f"- {f}" for f in facts)
        return f"<execution_state>\n{body}\n</execution_state>"

    async def _observe_tool_result(self, call: ToolCall, tool_result_str: str) -> str:
        """Derive structured facts from a tool result and append URL diagnostics.

        Returns the (possibly augmented) tool result string. URL-health lines
        are appended so the agent sees reachability without an extra tool call.
        """
        name = call.name
        if name in (
            "run_command", "run_task", "shell_start", "shell_poll", "shell_stop",
            "run_in_terminal", "terminal_output", "terminal_stop",
        ):
            facts = extract_facts(tool_result_str)
            if name in ("run_command", "run_task", "shell_start", "run_in_terminal"):
                cmd = call.arguments.get("command", "")
                self._exec_state.add_command(cmd, facts.exit_code, ok=(facts.exit_code == 0 if facts.exit_code is not None else True))
            self._exec_state.add_artifacts(facts.artifacts)
            for pid in facts.background_pids:
                self._exec_state.add_pid(pid)
            # A poll repeats startup URLs after the process has had time to bind.
            results = await self._probe.probe_new(tool_result_str, retry_failed=name in ("shell_poll", "terminal_output"))
            if results:
                for probe_result in results:
                    self._exec_state.record_probe_result(probe_result)
                diag = "\n".join(r.to_line() for r in results)
                previews = "\n\n".join(
                    preview
                    for r in results
                    if (preview := r.preview_block())
                )
                sep = "\n" if tool_result_str and not tool_result_str.endswith("\n") else ""
                tool_result_str = f"{tool_result_str}{sep}\n{diag}\n"
                if previews:
                    tool_result_str += f"\n{previews}\n"
        elif name == "fetch_url":
            url = str(call.arguments.get("url", ""))
            if is_local_url(url):
                failed = tool_result_str.lstrip().startswith(("HTTP ", "Error fetching URL:"))
                result = ProbeResult(
                    advertised_url=url,
                    probe_url=normalize_probe_url(url),
                    ok=not failed,
                    status_code=0 if failed else 200,
                    error=tool_result_str[:200] if failed else "",
                )
                self._exec_state.record_probe_result(result)
        return tool_result_str

    def _append_post_edit_syntax_feedback(
        self,
        call: ToolCall,
        tool_result_str: str,
        paths: list[str],
    ) -> tuple[str, list[dict]]:
        """Validate eligible successful edits and return prompt feedback for failures."""
        if not self.tier.syntax_check_enabled or call.name not in (
            "edit_file", "edit_lines", "create_file",
        ):
            return tool_result_str, []
        if tool_result_str.lstrip().startswith(("Error", "Edit failed", "FAIL:")):
            return tool_result_str, []

        feedback_messages: list[dict] = []
        for edit_path in dict.fromkeys(paths):
            if not edit_path or not supports_immediate_syntax_check(edit_path):
                continue
            check_result = check_file_after_edit(
                edit_path,
                repo_path=self.registry.repo_path if hasattr(self.registry, "repo_path") else None,
                include_warnings=False,
            )
            error_text = format_errors_for_loop(check_result, include_warnings=False)
            if error_text:
                tool_result_str += f"\n{error_text}"
                feedback_messages.append({
                    "role": "system",
                    "content": (
                        f"<syntax_check>\n{error_text}\n</syntax_check>\n"
                        f"Read {edit_path} with read_file before attempting to correct these syntax errors."
                    ),
                })
            else:
                tool_result_str += f"\n[syntax-check] {edit_path}: valid"
        return tool_result_str, feedback_messages

    @staticmethod
    def _extract_task(messages: list[dict]) -> str:
        """Best-effort task text from the most recent user message."""
        for msg in reversed(messages):
            if msg.get("role") == "user" and msg.get("content"):
                c = msg["content"]
                return c if isinstance(c, str) else str(c)
        return ""

    @staticmethod
    def _is_transient_llm_error(err: Exception | str) -> bool:
        """Return whether an LLM/router failure is likely transient.

        Rate limits, timeouts, connection resets and gateway/5xx errors are
        worth retrying with backoff; anything else (auth, bad request,
        context-length — handled separately) is not.
        """
        msg = str(err).lower()
        return any(marker in msg for marker in (
            "429", "too many requests", "rate limit", "rate_limit",
            "timeout", "timed out", "connection", "econnreset",
            "502", "503", "504", "bad gateway", "service unavailable",
            "gateway timeout", "overloaded", "temporarily unavailable",
            "internal server error",
        ))

    def _missing_for_prompt(self) -> list[str]:
        """Human-readable summary of unresolved obligations, for honest reporting."""
        verdict = evaluate_completion(self._exec_state, has_text_response=False)
        return verdict.missing

    @staticmethod
    def _is_substantive_explanation(text: str) -> bool:
        """Check if a completion response is a substantive explanation.

        A bare "Done." / "Task complete." / "Changes applied." is NOT enough.
        The agent must explain WHAT it changed and WHY, so the user understands
        the work that was done.

        Returns True if the response appears to be a genuine explanation.
        """
        if not text or not text.strip():
            return False
        stripped = text.strip()
        # Too short to be substantive
        if len(stripped) < 30:
            return False
        lower = stripped.lower()
        # Bare completion markers without any detail
        bare = {
            "done.", "done", "task complete.", "complete.", "finished.",
            "changes applied.", "changes made.", "all done.", "that's it.",
            "thats it.", "i'm done.", "im done.", "finished!", "done!",
            "complete!", "task complete!", "completed.", "completed!",
            "work complete.", "all changes applied.", "all changes made.",
        }
        if stripped.lower() in bare or stripped.rstrip(".!") in bare:
            return False
        # Check for actual explanatory content — look for keywords that indicate
        # the agent is describing changes, files, reasons, etc.
        explanatory_keywords = {
            "file", "files", "changed", "modified", "added", "created",
            "updated", "implemented", "function", "method", "class",
            "because", "therefore", "reason", "since", "so that",
            "note", "notes", "caveat", "caveats", "follow", "verify",
            "check", "test", "tests", "run", "using", "approach",
            "solution", "fix", "fixes", "fixing", "improved", "refactored",
            "new", "removed", "deleted", "replaced", "moved", "renamed",
            "import", "imports", "export", "exports", "config", "configuration",
            "parameter", "parameters", "argument", "arguments", "return",
            "output", "input", "logic", "behavior", "behaviour",
            "handles", "handling", "error", "errors", "validation",
            "edge", "case", "cases", "scenario", "scenarios",
            "step", "steps", "first", "then", "next", "finally",
            "here", "below", "summary", "overview", "description",
            "details", "detail", "specifically", "specific", "particular",
            "includes", "including", "contains", "consists",
        }
        hits = sum(1 for kw in explanatory_keywords if kw in lower)
        # Require at least 2 explanatory keyword hits OR a multi-paragraph response
        paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
        if len(paragraphs) >= 2:
            return True
        # Single-line responses need strong keyword evidence to qualify
        if stripped.count("\n") == 0:
            return hits >= 3
        return hits >= 2

    def _paths_from_tool_call(self, call: ToolCall) -> list[str]:
        paths = []
        for key in ("path", "cwd", "file_path"):
            if key in call.arguments and isinstance(call.arguments[key], str):
                paths.append(call.arguments[key])
        # Also detect files from run_command (cat/grep/ls/head/tail/wc)
        if call.name == "run_command" and "command" in call.arguments:
            cmd = call.arguments["command"]
            # Match: cat|head|tail|wc|grep|ls|less|more filename
            for m in re.finditer(r'(?:cat|head|tail|wc|grep|ls|less|more|nano|vim)\s+(?:-\w+\s+)*["\']?([^\s"\'|;<>]+)["\']?', cmd):
                path = m.group(1)
                if path and path != "-":
                    paths.append(path)
            # Also match: < filename (redirect input)
            for m in re.finditer(r'<\s*["\']?([^\s"\'|;<>]+)["\']?', cmd):
                path = m.group(1)
                if path:
                    paths.append(path)
        elif call.name == "aider_edit":
            content = str(call.arguments.get("content", ""))
            paths.extend(
                match.group(1).strip()
                for match in re.finditer(r"(?m)^([^\n]+)\n<<<<<<< SEARCH$", content)
            )
        return paths

    # Tools that neither mutate state nor depend on each other within a
    # turn — safe to run concurrently. run_command is deliberately excluded
    # (arbitrary shell), as are all edit tools.
    _PARALLEL_SAFE_TOOLS = frozenset({
        "read_file", "view_image", "grep_code", "find_symbols", "get_structure",
        "glob_files", "list_dir", "web_search", "fetch_url", "lookup_docs",
        "get_function_body", "find_references", "get_symbol_definition",
        "get_function_dependencies", "get_callers", "get_ast_range",
    })

    # Result prefixes that mean "this tool is failing at the infrastructure
    # level" (not a wrong answer). Used to detect a tool that keeps failing
    # across turns so we can tell the model to stop hammering it.
    _FAILING_RESULT_MARKERS = (
        "Error", "Edit failed", "FAIL:", "HTTP ", "Search rate-limited", "Search timed out",
        "Search backend is cooling", "Search backend returned no results",
        "Fetch timed out", "[no content",
    )

    @classmethod
    def _result_is_failure(cls, result: str) -> bool:
        stripped = result.lstrip()
        return not stripped or stripped.startswith(cls._FAILING_RESULT_MARKERS)

    def _record_tool_outcome(self, name: str, result: str) -> str | None:
        """Track consecutive infrastructure failures per tool.

        Returns an intervention message once a tool has failed 3 turns in a
        row — models often keep rephrasing queries at a dead backend instead
        of changing tools, burning the whole turn budget.
        """
        failures = getattr(self, "_consecutive_tool_failures", None)
        if failures is None:
            failures = self._consecutive_tool_failures = {}
        # Circuit-breaker interceptions and supervisor gate denials are
        # GUIDANCE, not backend outages — never count them toward the streak.
        if result.startswith("CIRCUIT-BREAKER:") or "temporarily disabled by the supervisor" in result:
            failures[name] = 0
            return None
        if self._result_is_failure(result):
            failures[name] = failures.get(name, 0) + 1
            if failures[name] == 3:
                return (
                    f"SYSTEM NOTICE: `{name}` has now failed {failures[name]} times in a row "
                    f"with infrastructure errors (not wrong answers — the backend is "
                    f"unavailable). STOP calling `{name}`. Use a different tool, work "
                    f"with the information you already have, or produce your best "
                    f"final answer now."
                )
        else:
            failures[name] = 0
        return None

    # Web-research tools that constitute "reading in depth" (resets the
    # search-thrash counter) vs. "searching" (accumulates it).
    _WEB_READ_TOOLS = frozenset({"fetch_url", "lookup_docs"})

    def _record_research_progress(self, tool_calls: list[ToolCall]) -> str | None:
        """Detect the search-thrash failure mode: many web_search calls in a
        row without ever fetching/reading a result in depth.

        The loop detector only catches EXACT repeats; a weak model escapes it
        by rephrasing the same query dozens of times (observed: 43 searches, 4
        fetches, anchored on the wrong framing). This fires an intervention
        that forces a read, a reframe, or a committed answer. Keyed on
        web_search, so it never triggers on non-research (coding) tasks.
        """
        names = [tc.name for tc in tool_calls]
        if any(n in self._WEB_READ_TOOLS for n in names):
            # A read breaks the thrash — reset.
            self._searches_since_read = 0
            self._research_last_intervention = 0
            return None
        n_search = sum(1 for n in names if n == "web_search")
        if n_search == 0:
            return None
        since = getattr(self, "_searches_since_read", 0) + n_search
        self._searches_since_read = since
        last = getattr(self, "_research_last_intervention", 0)
        # Fire at 5 consecutive searches-without-read, then every +5.
        if since >= 5 and since - last >= 5:
            self._research_last_intervention = since
            # Enforcement, not advice: a system note alone is ignored by weak
            # models (observed: intervention fired ~9 times, obeyed 0). Remove
            # web_search from the toolset for the next 2 turns so it CANNOT
            # keep searching — it must read a result or answer.
            gates = getattr(self, "_tool_gates", None)
            if gates is None:
                gates = self._tool_gates = {}
            gates["web_search"] = [2, "search-without-reading thrash"]
            return (
                f"SYSTEM NOTICE: you have run {since} web searches in a row "
                f"WITHOUT reading any result in depth, and are not making "
                f"progress. web_search is now DISABLED for your next 2 turns. "
                f"Your framing is likely WRONG (wrong region, language, era, or "
                f"genre) — junk results are evidence of that, not bad wording. "
                f"You MUST do ONE of these NOW:\n"
                f"1. STOP and think: re-read the constraints and list NEW "
                f"candidate answers from your own knowledge in a different "
                f"region/language/era/genre than you have been assuming. Then "
                f"verify the best one.\n"
                f"2. fetch_url the single most promising result so far and READ "
                f"it fully — verify it against the constraints.\n"
                f"3. If you still cannot confirm, commit to your single best "
                f"guess as the final answer now."
            )
        return None

    def _apply_tool_gates(self, tools: list[dict] | None) -> list[dict] | None:
        """Strip gated tools from the schemas offered to the model.

        Gates live in ``self._tool_gates`` (tool -> [turns_left, reason]) and
        are armed by the web-search anti-thrash logic and by advisor vetoes.
        Each call decrements the counters once per turn; expired gates move
        to a cooldown so the same tool cannot be re-vetoed immediately.
        """
        if not tools:
            return tools
        if not self.router.role_supports_vision(self.role):
            tools = [tool for tool in tools if tool.get("name") != "view_image"]
        gates = getattr(self, "_tool_gates", None) or {}
        cooldown = getattr(self, "_veto_cooldown", None)
        if cooldown is None:
            cooldown = self._veto_cooldown = {}
        # Tick cooldowns down every turn regardless of gate activity.
        for name in list(cooldown):
            cooldown[name] = max(0, cooldown[name] - 1)
            if cooldown[name] <= 0:
                del cooldown[name]
        if not gates:
            return tools
        active: list[str] = []
        for name in list(gates):
            turns, _reason = gates[name]
            if turns <= 0:
                del gates[name]
                cooldown[name] = self._VETO_COOLDOWN_TURNS
                continue
            active.append(name)
            gates[name][0] = turns - 1
        if not active:
            return tools
        return [t for t in tools if t.get("name") not in active]

    def _batch_is_parallel_safe(self, tool_calls: list[ToolCall]) -> bool:
        return all(tc.name in self._PARALLEL_SAFE_TOOLS for tc in tool_calls)

    def _gate_denial(self, name: str) -> str | None:
        """Return a denial message when *name* is currently tool-gated."""
        gates = getattr(self, "_tool_gates", None) or {}
        entry = gates.get(name)
        if not entry:
            return None
        turns, reason = entry
        return (
            f"Error: tool '{name}' is temporarily disabled by the supervisor "
            f"({turns} turn(s) left): {reason}. Proceed with other tools — "
            f"do NOT retry '{name}' until it is re-enabled."
        )

    async def _execute_batch(self, tool_calls: list[ToolCall]) -> list[str]:
        """Execute a turn's tool calls; concurrently when all are read-only.

        I/O-bound lookups (web fetches, searches, file reads) overlap, so a
        batch of three 5s fetches costs ~5s instead of 15s. Results are
        returned in call order either way. Gated tools are denied without
        executing (the model may emit calls for tools no longer in schema).
        """
        async def _run_one(tc: ToolCall) -> str:
            denial = self._gate_denial(tc.name)
            if denial is not None:
                return denial
            return await self.registry.safe_execute(tc.name, tc.arguments)

        if len(tool_calls) > 1 and self._batch_is_parallel_safe(tool_calls):
            gathered = await asyncio.gather(
                *[_run_one(tc) for tc in tool_calls],
                return_exceptions=True,
            )
            return [
                r if isinstance(r, str) else f"Error executing {tc.name}: {r}"
                for tc, r in zip(tool_calls, gathered)
            ]
        results: list[str] = []
        for tc in tool_calls:
            try:
                results.append(await _run_one(tc))
            except Exception as e:
                results.append(f"Error executing {tc.name}: {e}")
        return results

    async def run_tool_loop(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_turns: int | None = None,
        edit_mode: bool = False,
        disable_reasoning: bool = False,
        max_seconds: float | None = None,
    ) -> ExecutionResult:
        result = ExecutionResult(success=True, output="")
        current_messages = list(messages)
        logger.debug("run_tool_loop start (msgs=%d)", len(messages))
        history_turns: list[dict] = []
        # No turn budget by default: the loop runs until the task completes or
        # the loop detector proves a genuine infinite loop (byte-identical
        # turns). Callers may still pass an explicit max_turns.
        effective_max = max_turns
        # Optional wall-clock deadline. Independent of the turn cap: whichever
        # hits first stops the loop and forces an answer from the evidence so
        # far. Prevents pathological multi-minute runs on hard research.
        loop_start = time.monotonic()
        turn = 0
        self._exec_state.task = self._extract_task(messages)

        def _time_limit_result():
            result.disposition = CompletionDisposition.INCOMPLETE
            result.state_facts = self._exec_state.facts_for_prompt()
            result.missing_evidence = self._missing_for_prompt()
            result.trace.append(TraceEvent(
                phase="done",
                detail=f"time limit ({max_seconds:.0f}s) reached, stopping",
            ))
            if not result.output:
                result.output = (
                    f"Reached the {max_seconds:.0f}s research time limit. "
                    f"Answering from the evidence gathered so far."
                )
            result.history_turns = history_turns
            return result

        while True:
            remaining = None
            if max_seconds is not None:
                remaining = max_seconds - (time.monotonic() - loop_start)
                if remaining <= 0:
                    return _time_limit_result()
            def _make_progress(progress_text: str):
                result.trace.append(TraceEvent(phase="llm_progress", detail=progress_text))

            try:
                # In-loop context guard: compact the active conversation BEFORE
                # it outgrows the model's real context window. Without this the
                # first sign of overflow was the provider aborting the loop
                # mid-task ("agent stops for no reason").
                await self._ctx_guard.ensure_fits(current_messages, self.router)
                self._persist_guard_summary(history_turns)
                # Inject deterministic execution-state facts so the model does
                # not waste turns re-discovering what it already did (e.g. the
                # path of a just-built .exe, or that a dev server is reachable).
                state_block = self._state_block()
                if state_block and (not current_messages or current_messages[-1].get("role") != "system" or "<execution_state>" not in (current_messages[-1].get("content") or "")):
                    request_messages = current_messages + [{"role": "system", "content": state_block}]
                else:
                    request_messages = current_messages
                request_messages = guard_messages(request_messages)
                request_tools = self._apply_tool_gates(tools)
                _gen = self.router.generate(
                    role=self.role,
                    messages=request_messages,
                    tools=request_tools,
                    max_tokens=self.tier.default_max_tokens,
                    progress_callback=_make_progress,
                )
                # HARD deadline: cap the individual call to the remaining budget
                # so a single long reasoning call can't blow past max_seconds.
                if remaining is not None:
                    response = await asyncio.wait_for(_gen, timeout=remaining)
                else:
                    response = await _gen
                # Calibrate the guard's estimate with the provider's real
                # prompt_tokens so the next turn's threshold check is accurate.
                _usage = getattr(response, "usage", None)
                if _usage:
                    self._ctx_guard.calibrate(current_messages, _usage.get("prompt_tokens", 0))
            except asyncio.TimeoutError:
                if max_seconds is not None:
                    return _time_limit_result()
                # No wall-clock deadline: a bare provider/transport timeout is
                # transient — retry with backoff instead of aborting the turn.
                # (Previously this crashed formatting max_seconds=None.)
                if self._llm_error_retries < 4:
                    self._llm_error_retries += 1
                    delay = min(2.0 * (2 ** self._llm_error_retries), 30.0)
                    logger.warning("LLM call timed out (turn=%d); retrying in %.0fs", turn, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error("LLM router error: request timed out")
                return ExecutionResult(
                    success=False,
                    output="LLM error: request timed out",
                    history_turns=history_turns,
                    disposition=CompletionDisposition.INCOMPLETE,
                )
            except Exception as e:
                err_msg = str(e)
                # Context-length overflow: compact hard and retry once instead
                # of aborting the whole task.
                if is_context_length_error(err_msg) and self._ctx_error_retries < 2:
                    self._ctx_error_retries += 1
                    freed = await self._ctx_guard.force_compact(current_messages, self.router)
                    self._persist_guard_summary(history_turns)
                    logger.warning("context length exceeded; force-compacted ~%d tokens and retrying", freed)
                    result.trace.append(TraceEvent(phase="context_guard", detail=f"provider rejected oversized request; compacted ~{freed} tokens, retrying"))
                    continue
                # Transient provider failures (429/rate limits, timeouts,
                # connection resets, 5xx): retry with backoff instead of
                # killing the turn. A single hiccup on the FIRST call used to
                # abort the whole loop and surface as a bogus "tool-turn
                # budget" error even though zero tool turns had happened.
                if self._is_transient_llm_error(err_msg) and self._llm_error_retries < 4:
                    self._llm_error_retries += 1
                    delay = min(2.0 * (2 ** self._llm_error_retries), 30.0)
                    logger.warning("transient LLM error (turn=%d): %s — retrying in %.0fs", turn, err_msg, delay)
                    result.trace.append(TraceEvent(
                        phase="llm_retry",
                        detail=f"transient LLM error, retry {self._llm_error_retries}/4 in {delay:.0f}s",
                    ))
                    await asyncio.sleep(delay)
                    continue
                logger.error("LLM router error: %s", err_msg)
                return ExecutionResult(
                    success=False,
                    output=f"LLM error: {err_msg}",
                    history_turns=history_turns,
                    disposition=CompletionDisposition.INCOMPLETE,
                )

            turn += 1
            self._llm_error_retries = 0

            # --- Explicit turn cap (only when a caller passed max_turns) ---
            if effective_max is not None and turn >= effective_max and response.tool_calls:
                # The model still wants to call tools but we are out of budget.
                # Report honestly rather than silently accepting incomplete work.
                result.disposition = CompletionDisposition.TURN_LIMIT
                result.output = response.content or (
                    "Reached the maximum number of tool turns before the task finished. "
                    "The work above may be incomplete."
                )
                result.missing_evidence = self._missing_for_prompt()
                result.state_facts = self._exec_state.facts_for_prompt()
                result.trace.append(TraceEvent(
                    phase="done",
                    detail=f"max_turns ({effective_max}) reached with pending tool calls",
                ))
                result.history_turns = history_turns
                return result

            if not response.tool_calls:
                has_done_work = bool(result.files_modified)
                # --- Truncation recovery ---
                # The model hit the max_tokens limit and produced no usable tool
                # call (its output was cut off mid-generation). Do NOT treat this
                # as a completion — it wastes the turn. Nudge it to continue and
                # emit a concrete tool call. Bump the per-call budget a bit so a
                # large edit_file (full-file rewrite) can actually fit.
                if (
                    _response_was_truncated(getattr(response, "finish_reason", ""))
                    and self._truncation_retries < 10
                ):
                    self._truncation_retries += 1
                    nudged_max = min(self.tier.default_max_tokens + 2048, 32768)
                    force_msg = (
                        "<system_note>\n"
                        "Your previous response was cut off by the token limit before you could "
                        "emit a tool call. Do NOT repeat the long preamble. Continue now by "
                        "calling exactly ONE tool. If you were writing a file, prefer smaller "
                        "edit_file SEARCH/REPLACE blocks instead of rewriting the whole file.\n"
                        "</system_note>"
                    )
                    interrupted_message = {"role": "assistant", "content": response.content}
                    if response.reasoning_content:
                        interrupted_message["reasoning_content"] = response.reasoning_content
                    current_messages.append(interrupted_message)
                    current_messages.append({"role": "system", "content": force_msg})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="response truncated (max_tokens), retrying with nudged budget"))
                    self.tier.default_max_tokens = nudged_max
                    continue
                if edit_mode and not has_done_work and self._can_force_continuation():
                    force_msg = (
                        "<system_note>\n"
                        "CRITICAL: You are in an EDIT step. Do NOT output text-only responses. "
                        "You MUST call edit_file, edit_lines, or create_file to write code. "
                        "The file contents are already provided above — use them directly.\n"
                        "</system_note>"
                    )
                    current_messages.append({"role": "assistant", "content": response.content})
                    current_messages.append({"role": "system", "content": force_msg})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="text-only in edit_mode, forcing continuation"))
                    continue
                # A bare completion marker before any implementation work is
                # not a successful answer. This covers generic coding tasks
                # that do not have build/server evidence requirements.
                if _is_premature_completion(response.content or "", self._exec_state, has_done_work) and self._can_force_continuation():
                    force_msg = (
                        "<system_note>\n"
                        "You reported completion before implementing the requested change. "
                        "Do NOT stop yet. Inspect the relevant files and make the requested "
                        "edit with the available tools, then verify it.\n"
                        "</system_note>"
                    )
                    current_messages.append({"role": "assistant", "content": response.content})
                    current_messages.append({"role": "system", "content": force_msg})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="premature completion marker, forcing implementation"))
                    continue
                # A substantive text answer after doing tool work is a legitimate
                # completion (e.g. read-only exploration / Q&A). Only force a
                # continuation when the model gave up with NO content — that's
                # the "premature Done" case we want to push back on.
                if not has_done_work and result.tool_calls_made > 0 and not (response.content or "").strip() and self._can_force_continuation():
                    force_msg = (
                        "<system_note>\n"
                        "You produced an empty response but the task is not yet complete — "
                        "no files were modified. Do NOT report 'Done' or give up. "
                        "Continue working using your available tools. "
                        "If you are stuck, try a different approach or read more context.\n"
                        "</system_note>"
                    )
                    current_messages.append({"role": "assistant", "content": response.content})
                    current_messages.append({"role": "system", "content": force_msg})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="text-only without completing work, forcing continuation"))
                    continue
                # --- Test-run nudge ---
                # If the agent modified files but never executed any command this
                # session, it likely finished without verifying. For coding tasks
                # with a test suite present, running the tests is the single most
                # valuable verification step. Nudge once before accepting completion.
                if (
                    has_done_work
                    and not self._verification_ran
                    and not self._test_nudge_used
                    and self._repo_has_tests()
                ):
                    self._test_nudge_used = True
                    nudge = (
                        "<system_note>\n"
                        "Before finishing, VERIFY your changes by running the test suite or a "
                        "syntax/lint check with the run_command tool (e.g. `pytest -q` for "
                        "Python projects). Read the output and fix any failures you see. "
                        "Only report 'Done' once the checks pass (or you have documented why "
                        "they cannot be run).\n"
                        "</system_note>"
                    )
                    current_messages.append({"role": "assistant", "content": response.content})
                    current_messages.append({"role": "system", "content": nudge})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="nudging test-run verification before completion"))
                    continue
                # --- Evidence-aware completion gate ---
                # Before accepting a text-only "Done", check that the task's
                # required evidence (built artifact, reachable server) is
                # actually present. If not, nudge once per missing category.
                verdict = evaluate_completion(self._exec_state, has_text_response=bool((response.content or "").strip()))
                if not verdict.accept:
                    if self._can_force_continuation():
                        current_messages.append({"role": "assistant", "content": response.content})
                        current_messages.append({"role": "system", "content": f"<system_note>\n{verdict.nudge}\n</system_note>"})
                        result.trace.append(TraceEvent(phase="anti_loop", detail=f"completion gate: missing {verdict.missing}"))
                        continue
                    # Out of forced continuations — accept honestly as
                    # incomplete instead of deadlocking the loop forever
                    # (e.g. the task has no build step to produce evidence).
                    result.disposition = CompletionDisposition.INCOMPLETE
                    result.missing_evidence = verdict.missing
                    result.evidence = self._exec_state.facts_for_prompt()
                    result.output = response.content or ""
                    if response.content:
                        history_turns.append({"role": "assistant", "content": response.content})
                    result.trace.append(TraceEvent(
                        phase="done",
                        detail=f"accepted with missing evidence: {verdict.missing}",
                    ))
                    result.history_turns = history_turns
                    return result
                # --- Substantive explanation gate ---
                # The agent MUST end with a user-facing explanation of what it
                # changed and why. A bare "Done." / "Task complete." / short
                # acknowledgement after making edits is NOT acceptable — the
                # user needs to understand what was actually done. Force the
                # agent to produce a real summary before we accept completion.
                if has_done_work and not self._is_substantive_explanation(response.content or "") and self._can_force_continuation():
                    current_messages.append({"role": "assistant", "content": response.content})
                    current_messages.append({"role": "system", "content": (
                        "<system_note>\n"
                        "Do NOT report 'Done' yet. You have modified files but have not explained "
                        "your changes. Before finishing, provide a clear summary of:\n"
                        "1. What files you modified and what changed in each.\n"
                        "2. Why you made those changes (how they address the task).\n"
                        "3. Any caveats, follow-ups, or things the user should verify.\n"
                        "Then you may finish. Do not call any more tools — just explain.\n"
                        "</system_note>"
                    )})
                    result.trace.append(TraceEvent(phase="anti_loop", detail="forcing substantive explanation before completion"))
                    continue
                # Disposition: verified if evidence categories were satisfied,
                # declared otherwise (e.g. read-only Q&A with no evidence need).
                cats = classify_task(self._exec_state)
                if cats and any(c in ("build", "server") for c in cats):
                    result.disposition = CompletionDisposition.VERIFIED
                else:
                    result.disposition = CompletionDisposition.DECLARED
                result.evidence = self._exec_state.facts_for_prompt()
                result.output = response.content
                if response.content:
                    history_turns.append({"role": "assistant", "content": response.content})
                result.trace.append(TraceEvent(phase="done", detail="agent finished", payload={"content": response.content[:200]}))
                result.history_turns = history_turns
                return result

            result.tool_calls_made += len(response.tool_calls)

            if response.content and not result.output:
                result.output = response.content

            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            current_messages.append(assistant_msg)
            history_turns.append(assistant_msg)

            turn_files_read: list[str] = []
            turn_files_modified: list[str] = []

            # Execute the batch concurrently when every call is read-only
            # (searches, fetches, file reads are I/O-bound and independent —
            # three 5s web fetches overlap into ~5s instead of 15s). Any
            # mutating call in the batch forces sequential execution to
            # preserve ordering guarantees.
            executed = await self._execute_batch(response.tool_calls)

            turn_model_content: list[dict] = []
            for call, tool_result_str in zip(response.tool_calls, executed):
                model_content = getattr(tool_result_str, "model_content", None)
                if model_content:
                    turn_model_content.extend(model_content)
                logger.info("tool %s(%s) => %d chars", call.name,
                            str(call.arguments)[:80], len(tool_result_str))

                # Derive execution-state facts (commands, artifacts, URL health)
                # and append URL-probe diagnostics to the result the agent sees.
                tool_result_str = await self._observe_tool_result(call, tool_result_str)

                result.trace.append(TraceEvent(
                    phase="tool_call",
                    detail=call.name,
                    payload={"args": call.arguments, "result_preview": tool_result_str[:300]},
                ))

                paths = self._paths_from_tool_call(call)
                tool_result_str, syntax_messages = self._append_post_edit_syntax_feedback(
                    call,
                    tool_result_str,
                    paths,
                )
                if self._is_verification_call(call, tool_result_str):
                    self._verification_ran = True
                if call.name in ("read_file", "grep_code", "find_symbols", "get_structure", "glob_files", "list_dir",
                                 "get_function_body", "find_references", "get_symbol_definition",
                                 "get_function_dependencies", "get_callers", "get_ast_range"):
                    turn_files_read.extend(paths)
                    result.files_read.extend(paths)
                elif call.name in ("edit_file", "edit_lines", "create_file", "delete_file", "aider_edit") and not self._result_is_failure(tool_result_str):
                    self._verification_ran = False
                    turn_files_modified.extend(paths)
                    result.files_modified.extend(paths)
                # Shell-based writes are NOT parsed from the command string
                # here — the snapshot tracker (core/fs_state.py) detects the
                # actual byte-level mutations and surfaces them below.

                # Preserve the exact result in canonical history. Later model
                # calls receive a separately bounded replay projection.
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": tool_result_str,
                })
                history_turns.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": tool_result_str,
                })

                # A tool failing repeatedly at the infrastructure level gets a
                # hard system intervention — result-text guidance alone is
                # demonstrably ignored (models rephrase and retry forever).
                intervention = self._record_tool_outcome(call.name, tool_result_str)
                if intervention:
                    current_messages.append({"role": "system", "content": intervention})
                    history_turns.append({"role": "system", "content": intervention})
                    result.trace.append(TraceEvent(phase="tool_failure_streak", detail=intervention[:120]))

                for syntax_msg in syntax_messages:
                    current_messages.append(syntax_msg)
                    history_turns.append(syntax_msg)
                    result.trace.append(TraceEvent(
                        phase="syntax_check",
                        detail=f"Syntax errors found after {call.name}",
                    ))
            if turn_model_content:
                image_msg = {"role": "user", "content": turn_model_content}
                current_messages.append(image_msg)
                history_turns.append(image_msg)

            # Attribute real filesystem mutations detected by the snapshot
            # tracker (shell writes etc.) once per turn, deduped across turns.
            fs_tracker = getattr(self.registry, "fs_tracker", None)
            if fs_tracker is not None:
                new_fs = fs_tracker.changed_files() - self._fs_seen
                if new_fs:
                    self._fs_seen |= new_fs
                    for f in sorted(new_fs):
                        turn_files_modified.append(f)
                        result.files_modified.append(f)
                    self._exec_state.files_modified.update(new_fs)

            # Research anti-thrash: once per turn, check for the search-without-
            # reading spiral and force a read / reframe / commit.
            research_nudge = self._record_research_progress(response.tool_calls)
            if research_nudge:
                current_messages.append({"role": "system", "content": research_nudge})
                history_turns.append({"role": "system", "content": research_nudge})
                result.trace.append(TraceEvent(phase="research_thrash", detail=research_nudge[:120]))

            # Advisor check-in: every N turns the advisor reviews the recent
            # trajectory and injects approvals/criticisms/ideas mid-loop.
            advisor_feedback = await self._maybe_advisor_checkin(turn, history_turns, current_messages)
            if advisor_feedback:
                result.trace.append(TraceEvent(
                    phase="advisor_checkin",
                    detail=f"Advisor feedback injected (turn {turn})",
                    payload={"advisor_feedback": advisor_feedback, "turn": turn, "model": self.advisor_model},
                ))
            veto_note = getattr(self, "_last_veto_note", "")
            if veto_note:
                self._last_veto_note = ""
                result.trace.append(TraceEvent(
                    phase="advisor_veto",
                    detail=veto_note,
                    payload={"veto": veto_note, "turn": turn, "model": self.advisor_model},
                ))

            # Trajectory reduction (AgentDiet): compress older tool results in
            # the active conversation when it approaches the context budget.
            # No-op while well under budget; never touches the protected window.
            self._trajectory_pruner.maybe_prune(current_messages)

            # Turn-budget pressure: one-time nudge at 80% of the turn budget.
            budget_note = self._maybe_budget_nudge(turn, effective_max)
            if budget_note:
                current_messages.append({"role": "system", "content": budget_note})
                result.trace.append(TraceEvent(
                    phase="budget_nudge",
                    detail=f"turn-budget nudge at turn {turn}/{effective_max}",
                ))

            loop_check = self._loop_detector.record(
                response.tool_calls,
                files_read=turn_files_read,
                files_modified=turn_files_modified,
            )
            if loop_check.severity == "critical":
                # Unambiguously stuck (e.g. re-reading the exact same chunk 12+
                # times). Stop the loop now instead of burning more turns/tokens.
                result.trace.append(TraceEvent(phase="anti_loop", detail=f"CRITICAL: {loop_check.reason}"))
                if not result.output:
                    result.output = (
                        f"Stopped the tool loop to prevent a hang: {loop_check.reason}"
                    )
                result.disposition = CompletionDisposition.INCOMPLETE
                result.state_facts = self._exec_state.facts_for_prompt()
                result.history_turns = history_turns
                return result
            if loop_check.severity == "warning":
                warning_msg = (
                    f"<system_note>\n"
                    f"WARNING: {loop_check.reason}\n"
                    f"Progress so far:\n{self._loop_detector.progress_summary()}\n"
                    f"</system_note>"
                )
                if loop_check.reason != self._last_loop_warning_reason:
                    current_messages.append({"role": "system", "content": warning_msg})
                    self._last_loop_warning_reason = loop_check.reason
                result.trace.append(TraceEvent(phase="anti_loop", detail=loop_check.reason))

            # --- Explicit turn cap (only when a caller passed max_turns) ---
            if effective_max is not None and turn >= effective_max:
                result.disposition = CompletionDisposition.TURN_LIMIT
                result.state_facts = self._exec_state.facts_for_prompt()
                result.missing_evidence = self._missing_for_prompt()
                result.trace.append(TraceEvent(
                    phase="done",
                    detail=f"max_turns ({effective_max}) reached, stopping gracefully",
                ))
                if not result.output:
                    result.output = (
                        f"Reached the maximum number of tool turns ({effective_max}). "
                        f"Stopping here with the work completed so far."
                    )
                result.history_turns = history_turns
                return result

    async def run_tool_loop_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_turns: int | None = None,
        disable_reasoning: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        current_messages = list(messages)
        streaming_ok = True
        self._last_history_turns = []

        self._loop_files_modified: set[str] = set()
        self._ever_made_tool_calls = False
        # No turn budget by default: the loop runs until the task completes or
        # the loop detector proves a genuine infinite loop (byte-identical
        # turns). Callers may still pass an explicit max_turns.
        effective_max = max_turns
        turn = 0
        self._exec_state.task = self._extract_task(messages)
        final_disposition: CompletionDisposition | None = None
        final_evidence: list[str] = []
        final_missing: list[str] = []

        while True:
            stuck_reason: str | None = None

            while True:
                collected_content = ""
                collected_reasoning = ""
                collected_tool_calls: list[ToolCall] = []
                turn_usage: dict = {}
                got_chunks = False
                finish_reason = ""

                yield StreamChunk(progress_label="⏳ Contacting LLM...")

                # In-loop context guard: compact the active conversation BEFORE
                # it outgrows the model's real context window. Without this the
                # first sign of overflow was the provider aborting the loop
                # mid-task ("agent stops for no reason").
                await self._ctx_guard.ensure_fits(current_messages, self.router)
                self._persist_guard_summary_stream()

                # --- Step 1: Try streaming ---
                # Inject execution-state facts so the model doesn't re-discover
                # what it already did (artifact paths, reachable URLs, etc.).
                state_block = self._state_block()
                if state_block and (not current_messages or current_messages[-1].get("role") != "system" or "<execution_state>" not in (current_messages[-1].get("content") or "")):
                    request_messages = current_messages + [{"role": "system", "content": state_block}]
                else:
                    request_messages = current_messages
                request_messages = guard_messages(request_messages)
                request_tools = self._apply_tool_gates(tools)
                if streaming_ok:
                    stream_timeout = max(self.tier.default_max_tokens // 2, 60)
                    agen = self.router.generate_stream(
                        role=self.role,
                        messages=request_messages,
                        tools=request_tools,
                        max_tokens=self.tier.default_max_tokens,
                        disable_reasoning=disable_reasoning,
                    )
                    try:
                        while True:
                            # Per-chunk timeout: asyncio.wait_for awaits the
                            # __anext__() coroutine, which is the correct usage.
                            # (Wrapping the whole async generator in wait_for is
                            # invalid — an async generator is not awaitable.)
                            try:
                                chunk = await asyncio.wait_for(
                                    agen.__anext__(),
                                    timeout=stream_timeout,
                                )
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError:
                                streaming_ok = False
                                break
                            got_chunks = True
                            if chunk.text:
                                collected_content += chunk.text
                                yield chunk
                            elif chunk.reasoning:
                                collected_reasoning += chunk.reasoning
                                yield chunk
                            elif chunk.done:
                                if chunk.tool_calls:
                                    collected_tool_calls = chunk.tool_calls
                                if chunk.usage:
                                    turn_usage = chunk.usage
                                if chunk.finish_reason:
                                    finish_reason = chunk.finish_reason
                    except Exception:
                        streaming_ok = False
                    finally:
                        # Always release the generator to avoid ResourceWarnings.
                        try:
                            await agen.aclose()
                        except Exception:
                            pass

                # --- Step 2: Non-streaming fallback ---
                stream_silent = got_chunks and not collected_content and not collected_tool_calls and not turn_usage
                if not got_chunks or not streaming_ok or stream_silent:
                    try:
                        response = await asyncio.wait_for(
                            self.router.generate(
                                role=self.role,
                                messages=request_messages,
                                tools=request_tools,
                                max_tokens=self.tier.default_max_tokens,
                                disable_reasoning=disable_reasoning,
                            ),
                            timeout=120,
                        )
                    except Exception as e:
                        # Context-length overflow: compact hard and retry once
                        # instead of aborting the whole task mid-flight.
                        if is_context_length_error(e) and self._ctx_error_retries < 2:
                            self._ctx_error_retries += 1
                            freed = await self._ctx_guard.force_compact(current_messages, self.router)
                            self._persist_guard_summary_stream()
                            logger.warning("context length exceeded; force-compacted ~%d tokens and retrying", freed)
                            yield StreamChunk(progress_label=f"🗜️ Context window full — compacted ~{freed} tokens, continuing")
                            continue
                        # Transient provider failures (429/rate limits,
                        # timeouts, connection resets, 5xx): retry with
                        # backoff instead of killing the turn. A single hiccup
                        # on the FIRST call used to abort the loop and surface
                        # as a bogus "tool-turn budget" error even though zero
                        # tool turns had happened.
                        if self._is_transient_llm_error(e) and self._llm_error_retries < 4:
                            self._llm_error_retries += 1
                            delay = min(2.0 * (2 ** self._llm_error_retries), 30.0)
                            yield StreamChunk(
                                progress_label=f"⏳ LLM temporarily unavailable — retrying in {delay:.0f}s (attempt {self._llm_error_retries}/4)"
                            )
                            await asyncio.sleep(delay)
                            continue
                        yield StreamChunk(
                            error=str(e),
                            done=True,
                            disposition=CompletionDisposition.INCOMPLETE,
                            missing_evidence=self._missing_for_prompt(),
                        )
                        return

                    if response.content:
                        collected_content = response.content
                    if response.tool_calls:
                        collected_tool_calls = response.tool_calls
                    if response.reasoning_content:
                        collected_reasoning = response.reasoning_content
                    turn_usage = response.usage
                    if getattr(response, "finish_reason", ""):
                        finish_reason = response.finish_reason

                # Calibrate the guard's estimate with the provider's real
                # prompt_tokens so the next turn's threshold check is accurate.
                if turn_usage:
                    self._ctx_guard.calibrate(current_messages, turn_usage.get("prompt_tokens", 0))
                # A successful response clears the transient-error backoff.
                self._llm_error_retries = 0

                # --- Step 3: Handle text-only responses ---
                if not collected_tool_calls:
                    # Streaming providers have already delivered this content
                    # incrementally. Only the non-streaming fallback needs to
                    # emit it here, and only after completion gates approve it.
                    content_was_streamed = streaming_ok and got_chunks
                    has_done_work = bool(self._loop_files_modified)
                    # --- Truncation recovery (streaming) ---
                    # The model hit the max_tokens limit and produced no usable
                    # tool call (its output was cut off). Nudge it to continue
                    # with a concrete tool call instead of accepting the cut-off
                    # text as completion.
                    if _response_was_truncated(finish_reason) and self._truncation_retries < 10:
                        self._truncation_retries += 1
                        self.tier.default_max_tokens = min(self.tier.default_max_tokens + 2048, 32768)
                        force_msg = (
                            "<system_note>\n"
                            "Your previous response was cut off by the token limit before you could "
                            "emit a tool call. Do NOT repeat the long preamble. Continue now by "
                            "calling exactly ONE tool. Prefer smaller edit_file SEARCH/REPLACE "
                            "blocks instead of rewriting the whole file.\n"
                            "</system_note>"
                        )
                        interrupted_message = {"role": "assistant", "content": collected_content}
                        if collected_reasoning:
                            interrupted_message["reasoning_content"] = collected_reasoning
                        current_messages.append(interrupted_message)
                        current_messages.append({"role": "system", "content": force_msg})
                        yield StreamChunk(text="\n[Response truncated — continuing]\n")
                        continue
                    # A substantive text answer after doing tool work is a
                    # legitimate completion (read-only exploration / Q&A). Only
                    # force a continuation when the model gave up with NO
                    # content — the "premature Done" case.
                    if not has_done_work and self._ever_made_tool_calls and not (collected_content or "").strip() and self._can_force_continuation():
                        force_msg = (
                            "<system_note>\n"
                            "You produced an empty response but the task is not yet complete — "
                            "no files were modified. Do NOT report 'Done' or give up. "
                            "Continue working using your available tools. "
                            "If you are stuck, try a different approach or read more context.\n"
                            "</system_note>"
                        )
                        current_messages.append({"role": "assistant", "content": collected_content})
                        current_messages.append({"role": "system", "content": force_msg})
                        yield StreamChunk(text="\n[Continuing — task not yet completed]\n")
                        continue
                    if _is_premature_completion(collected_content or "", self._exec_state, has_done_work) and self._can_force_continuation():
                        force_msg = (
                            "<system_note>\n"
                            "You reported completion before implementing the requested change. "
                            "Do NOT stop yet. Inspect the relevant files and make the requested "
                            "edit with the available tools, then verify it.\n"
                            "</system_note>"
                        )
                        current_messages.append({"role": "assistant", "content": collected_content})
                        current_messages.append({"role": "system", "content": force_msg})
                        yield StreamChunk(progress_label="Completion reported too early; continuing implementation...")
                        continue
                    # --- Test-run nudge (streaming) ---
                    if (
                        has_done_work
                        and not self._verification_ran
                        and not self._test_nudge_used
                        and self._repo_has_tests()
                    ):
                        self._test_nudge_used = True
                        nudge = (
                            "<system_note>\n"
                            "Before finishing, VERIFY your changes by running the test suite or a "
                            "syntax/lint check with the run_command tool (e.g. `pytest -q` for "
                            "Python projects). Read the output and fix any failures you see. "
                            "Only report 'Done' once the checks pass (or you have documented why "
                            "they cannot be run).\n"
                            "</system_note>"
                        )
                        current_messages.append({"role": "assistant", "content": collected_content})
                        current_messages.append({"role": "system", "content": nudge})
                        yield StreamChunk(text="\n[Run the tests to verify before finishing]\n")
                        continue
                    # --- Evidence-aware completion gate (streaming) ---
                    # Before accepting a text-only "Done", require observed
                    # evidence for build/package and server tasks.
                    verdict = evaluate_completion(self._exec_state, has_text_response=bool((collected_content or "").strip()))
                    if not verdict.accept and self._can_force_continuation():
                        current_messages.append({"role": "assistant", "content": collected_content})
                        current_messages.append({"role": "system", "content": f"<system_note>\n{verdict.nudge}\n</system_note>"})
                        yield StreamChunk(progress_label="Completion evidence missing; continuing verification...")
                        continue
                    # --- Substantive explanation gate (streaming) ---
                    # Same requirement as non-streaming: after making edits,
                    # the agent must produce a real explanation before we
                    # accept completion. A bare "Done." is not enough.
                    if self._loop_files_modified and not self._is_substantive_explanation(collected_content or "") and self._can_force_continuation():
                        current_messages.append({"role": "assistant", "content": collected_content})
                        current_messages.append({"role": "system", "content": (
                            "<system_note>\n"
                            "Do NOT report 'Done' yet. You have modified files but have not explained "
                            "your changes. Before finishing, provide a clear summary of:\n"
                            "1. What files you modified and what changed in each.\n"
                            "2. Why you made those changes (how they address the task).\n"
                            "3. Any caveats, follow-ups, or things the user should verify.\n"
                            "Then you may finish. Do not call any more tools — just explain.\n"
                            "</system_note>"
                        )})
                        yield StreamChunk(progress_label="Forcing substantive explanation before completion...")
                        continue
                    cats = classify_task(self._exec_state)
                    if not verdict.accept:
                        # Evidence gate could not be satisfied within the
                        # forced-continuation budget — report honestly as
                        # incomplete instead of deadlocking the loop forever
                        # (e.g. the task has no build step to produce evidence).
                        final_disposition = CompletionDisposition.INCOMPLETE
                        final_missing = verdict.missing
                    elif cats and any(c in ("build", "server") for c in cats):
                        final_disposition = CompletionDisposition.VERIFIED
                    else:
                        final_disposition = CompletionDisposition.DECLARED
                    final_evidence = self._exec_state.facts_for_prompt()
                    if collected_content:
                        self._last_history_turns.append({
                            "role": "assistant",
                            "content": collected_content,
                        })
                    if collected_content and not content_was_streamed:
                        words = collected_content.split(" ")
                        for i, word in enumerate(words):
                            yield StreamChunk(text=word if i == 0 else " " + word)
                            await asyncio.sleep(0.012)
                    yield StreamChunk(done=True, usage=turn_usage, finish_reason=finish_reason,
                                      disposition=final_disposition, evidence=final_evidence,
                                      missing_evidence=final_missing)
                    return

                # --- Step 4: Execute tool calls ---
                self._ever_made_tool_calls = True
                turn += 1

                assistant_msg = {
                    "role": "assistant",
                    "content": collected_content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in collected_tool_calls
                    ],
                }
                if collected_reasoning:
                    assistant_msg["reasoning_content"] = collected_reasoning
                current_messages.append(assistant_msg)
                self._last_history_turns.append(assistant_msg)

                turn_files_read: list[str] = []
                turn_files_modified: list[str] = []

                # Read-only batches execute concurrently (see _execute_batch);
                # announce all calls up front so the TUI shows the batch.
                _batch_results: list[str] | None = None
                if len(collected_tool_calls) > 1 and self._batch_is_parallel_safe(collected_tool_calls):
                    for call in collected_tool_calls:
                        yield StreamChunk(tool_calls=[call])
                    yield StreamChunk(
                        progress_label=f"⏳ {len(collected_tool_calls)} parallel lookups..."
                    )
                    _batch_results = await self._execute_batch(collected_tool_calls)

                turn_model_content: list[dict] = []
                for call_index, call in enumerate(collected_tool_calls):
                    if _batch_results is not None:
                        tool_result_str = _batch_results[call_index]
                        logger.info("tool %s(%s) => %d chars", call.name,
                                    str(call.arguments)[:80], len(tool_result_str))
                    else:
                        # Announce the tool call BEFORE executing so the TUI can
                        # show a live progress panel while the tool runs.
                        yield StreamChunk(tool_calls=[call])
                        yield StreamChunk(
                            progress_label=f"⏳ {call.name}({_short_args(call.arguments)})..."
                        )
                        try:
                            tool_result_str = await self.registry.safe_execute(call.name, call.arguments)
                            logger.info("tool %s(%s) => %d chars", call.name,
                                        str(call.arguments)[:80], len(tool_result_str))
                        except Exception as e:
                            tool_result_str = f"Error executing {call.name}: {e}"
                            yield StreamChunk(error=tool_result_str)

                    model_content = getattr(tool_result_str, "model_content", None)
                    if model_content:
                        turn_model_content.extend(model_content)

                    # Derive execution-state facts + append URL-health diagnostics
                    # so the agent sees reachability without an extra tool call.
                    tool_result_str = await self._observe_tool_result(call, tool_result_str)

                    paths = self._paths_from_tool_call(call)
                    tool_result_str, syntax_messages = self._append_post_edit_syntax_feedback(
                        call,
                        tool_result_str,
                        paths,
                    )
                    if self._is_verification_call(call, tool_result_str):
                        self._verification_ran = True
                    if call.name in ("read_file", "grep_code", "find_symbols", "get_structure", "glob_files", "list_dir",
                                     "get_function_body", "find_references", "get_symbol_definition",
                                     "get_function_dependencies", "get_callers", "get_ast_range"):
                        turn_files_read.extend(paths)
                        self._exec_state.files_read.update(paths)
                    elif call.name in ("edit_file", "edit_lines", "create_file", "delete_file", "aider_edit") and not self._result_is_failure(tool_result_str):
                        self._verification_ran = False
                        turn_files_modified.extend(paths)
                        self._loop_files_modified.update(paths)
                        self._exec_state.files_modified.update(paths)
                    elif call.name == "run_command":
                        cmd = call.arguments.get("command", "")
                        read_cmds = ("cat", "head", "tail", "wc", "less", "more", "grep", "find", "ls", "echo")
                        # Reads are tracked here (the tracker only sees mutations);
                        # shell WRITES are not parsed from the command string —
                        # the snapshot tracker (core/fs_state.py) detects the
                        # actual byte-level mutations and surfaces them below.
                        if any(cmd.strip().startswith(c) for c in read_cmds):
                            turn_files_read.extend(paths)
                            self._exec_state.files_read.update(paths)
                    elif call.name == "run_task" and call.arguments.get("save_output_to"):
                        target = str(call.arguments["save_output_to"])
                        turn_files_modified.append(target)
                        self._exec_state.files_modified.add(target)

                    # Keep exact tool output in the canonical turn history.
                    # Context assembly is responsible for producing a bounded
                    # replay projection when a later model call needs one.
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result_str,
                    })
                    self._last_history_turns.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": tool_result_str,
                    })

                    if tool_result_str:
                        yield StreamChunk(tool_result=tool_result_str)

                    for syntax_msg in syntax_messages:
                        current_messages.append(syntax_msg)
                        self._last_history_turns.append(syntax_msg)

                if turn_model_content:
                    image_msg = {"role": "user", "content": turn_model_content}
                    current_messages.append(image_msg)
                    self._last_history_turns.append(image_msg)

                # Attribute real filesystem mutations detected by the snapshot
                # tracker (shell writes etc.) once per turn, deduped across turns.
                fs_tracker = getattr(self.registry, "fs_tracker", None)
                if fs_tracker is not None:
                    new_fs = fs_tracker.changed_files() - self._fs_seen
                    if new_fs:
                        self._fs_seen |= new_fs
                        for f in sorted(new_fs):
                            turn_files_modified.append(f)
                            self._loop_files_modified.add(f)
                        self._exec_state.files_modified.update(new_fs)

                # Advisor check-in (mid-loop): inject the feedback note, hand
                # the full memo to the TUI as a permanent panel, and surface a
                # compact preview in the live activity label.
                advisor_feedback = await self._maybe_advisor_checkin(turn, self._last_history_turns, current_messages)
                if advisor_feedback:
                    preview = advisor_feedback.replace("\n", " ")[:120]
                    yield StreamChunk(
                        progress_label=f"🧭 Advisor (turn {turn}): {preview}",
                        advisor_feedback=advisor_feedback,
                        model=self.advisor_model,
                    )
                veto_note = getattr(self, "_last_veto_note", "")
                if veto_note:
                    self._last_veto_note = ""
                    yield StreamChunk(
                        progress_label=f"🚫 Advisor veto: {veto_note}",
                        model=self.advisor_model,
                    )

                # Trajectory reduction (AgentDiet): compress older tool results
                # in the active conversation when it nears the context budget.
                self._trajectory_pruner.maybe_prune(current_messages)

                # Turn-budget pressure: one-time nudge at 80% of the budget.
                budget_note = self._maybe_budget_nudge(turn, effective_max)
                if budget_note:
                    current_messages.append({"role": "system", "content": budget_note})
                    yield StreamChunk(
                        progress_label=f"⏳ Turn budget nearly exhausted ({turn}/{effective_max}) — converging",
                    )

                # --- Step 5: Check for loops ---
                loop_check = self._loop_detector.record(
                    collected_tool_calls,
                    files_read=turn_files_read,
                    files_modified=turn_files_modified,
                )
                if loop_check.severity == "warning":
                    warning_text = (
                        f"[ANTI-LOOP WARNING] {loop_check.reason}\n"
                        f"Progress so far:\n{self._loop_detector.progress_summary()}"
                    )
                    # Yield as error, NOT text. If yielded as text, the TUI
                    # accumulates it in text_acc and the Live display reprints
                    # the full buffer on every 20fps refresh, causing the
                    # warning to appear hundreds of times in the terminal.
                    yield StreamChunk(error=warning_text)
                    if loop_check.reason != self._last_loop_warning_reason:
                        warning_msg = (
                            f"<system_note>\n"
                            f"WARNING: {loop_check.reason}\n"
                            f"Progress so far:\n{self._loop_detector.progress_summary()}\n"
                            f"</system_note>"
                        )
                        current_messages.append({"role": "system", "content": warning_msg})
                        self._last_loop_warning_reason = loop_check.reason
                if loop_check.severity == "critical":
                    # Unambiguously stuck (e.g. re-reading the exact same chunk
                    # 12+ times). Stop streaming now instead of burning more
                    # turns/tokens. Emit a normal done chunk so the agent's
                    # synthesis path can summarize the work done so far.
                    yield StreamChunk(
                        text=f"\n[Stopping: {loop_check.reason}]\n",
                        done=True,
                        usage=turn_usage,
                        disposition=CompletionDisposition.INCOMPLETE,
                        evidence=self._exec_state.facts_for_prompt(),
                        missing_evidence=self._missing_for_prompt(),
                    )
                    return
                # --- Explicit turn cap (only when a caller passed max_turns) ---
                if effective_max is not None and turn >= effective_max:
                    yield StreamChunk(
                        error=f"Max tool turns ({effective_max}) reached — stopping to prevent an infinite loop.",
                        done=True,
                        usage=turn_usage,
                        disposition=CompletionDisposition.TURN_LIMIT,
                        evidence=self._exec_state.facts_for_prompt(),
                        missing_evidence=self._missing_for_prompt(),
                    )
                    return

    @property
    def last_history_turns(self) -> list[dict]:
        return self._last_history_turns

    def clear_last_history(self):
        self._last_history_turns = []

    @staticmethod
    def _extract_paths(args: dict) -> list[str]:
        paths = []
        for key in ("path", "cwd", "file_path"):
            if key in args and isinstance(args[key], str):
                paths.append(args[key])
        return paths

    def _repo_has_tests(self) -> bool:
        """Bounded recursive test discovery that skips dependency trees."""
        import os as _os
        repo = getattr(self.registry, "repo_path", None)
        if not repo:
            return False
        test_name = re.compile(
            r"^(?:test_.+|.+_test)\.py$|.+\.(?:test|spec)\.(?:js|jsx|ts|tsx)$|.+_test\.(?:go|rb)$"
        )
        excluded = {".git", ".zircon-code", "node_modules", ".venv", "venv", "__pycache__"}
        seen = 0
        for _root, dirs, files in _os.walk(str(repo)):
            dirs[:] = [name for name in dirs if name not in excluded]
            for name in files:
                seen += 1
                if test_name.match(name):
                    return True
                if seen >= 5000:
                    dirs[:] = []
                    break
            if seen >= 5000:
                break
        # package.json with a test script is also a strong signal
        pkg = _os.path.join(str(repo), "package.json")
        if _os.path.isfile(pkg):
            try:
                with open(pkg, encoding="utf-8", errors="replace") as fh:
                    if '"test"' in fh.read():
                        return True
            except Exception:
                pass
        return False

    @staticmethod
    def _is_verification_call(call: ToolCall, result: str) -> bool:
        """Recognize actual verification; ordinary shell exploration is not evidence."""
        if call.name not in ("run_command", "run_task"):
            return False
        command = str(call.arguments.get("command", "")).lower()
        patterns = (
            r"\bpytest\b", r"\bpyright\b", r"\bmypy\b", r"\bruff\b", r"\bflake8\b",
            r"\bnpm\s+(?:run\s+)?(?:test|build|lint|typecheck)\b",
            r"\bpnpm\s+(?:test|build|lint|typecheck)\b", r"\byarn\s+(?:test|build|lint|typecheck)\b",
            r"\bgo\s+test\b", r"\bcargo\s+(?:test|check|clippy|build)\b",
            r"\b(?:make|cmake)\s+(?:test|check|build)\b", r"\btsc\b",
        )
        return any(re.search(pattern, command) for pattern in patterns)
