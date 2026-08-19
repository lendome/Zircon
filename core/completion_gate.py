"""Evidence-aware completion gating.

The executor used to accept any non-empty model response as a successful
completion after tool activity. That let the agent stop with "Done." before a
build actually produced an artifact or a dev server was actually reachable.

This module classifies the task into evidence categories and, when the model
tries to stop with a text-only response, checks whether the required evidence
is present. If not, it returns a targeted continuation nudge (one per missing
category) instead of accepting the completion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .runtime_probe import ProbeResult, normalize_probe_url


_BUILD_VERBS = re.compile(
    r"\b(cargo\s+build|tauri\s+build|electron-?builder|pyinstaller|nuitka|"
    r"webpack|vite\s+build|tsc|dotnet\s+build|go\s+build|cmake|make\b|"
    r"npm\s+run\s+build|yarn\s+build|pnpm\s+build|build|compile|bundle|"
    r"package|ship|release)\b",
    re.IGNORECASE,
)

_PACKAGE_KEYWORDS = re.compile(
    r"\b(exe|executable|\.msi|installer|bundle|native\s+binary|"
    r"desktop\s+app|ship|release\s+build|standalone\s+binary|"
    r"build\s+(?:it|as|the)\s+\w*exe)\b",
    re.IGNORECASE,
)

_SERVER_START_RE = re.compile(
    r"\b(shell_start|dev\s+server|npm\s+run\s+dev|vite|flask|fastapi|"
    r"uvicorn|gunicorn|express|next\s+dev|next\s+start|serve|http\.server|"
    r"php\s+-S|rails\s+s|python\s+-m\s+http)\b",
    re.IGNORECASE,
)

_SERVER_TASK_RE = re.compile(
    r"\b(server|dev\s+server|run\s+the\s+app|launch\s+the\s+app|"
    r"start\s+the\s+(app|server|api)|web\s+app|webapp|web\s+server|"
    r"api\s+server|localhost|127\.0\.0\.1)\b",
    re.IGNORECASE,
)


@dataclass
class ExecutionState:
    """Structured facts derived from every tool call during a loop run.

    The active conversation keeps full tool results; this object holds only the
    concise, deterministic facts the model would otherwise have to re-discover
    by calling tools again (file contents it already read, command exit codes,
    server health, discovered artifacts).
    """
    task: str = ""
    commands: list[dict[str, Any]] = field(default_factory=list)
    files_modified: set[str] = field(default_factory=set)
    files_read: set[str] = field(default_factory=set)
    artifacts: list[str] = field(default_factory=list)
    background_pids: list[str] = field(default_factory=list)
    probe_results: list[ProbeResult] = field(default_factory=list)
    _latest_probes: dict[str, ProbeResult] = field(default_factory=dict)
    server_started: bool = False
    nudges_used: set[str] = field(default_factory=set)

    def add_command(self, command: str, exit_code: int | None, ok: bool) -> None:
        self.commands.append({"command": command, "exit_code": exit_code, "ok": ok})
        if _SERVER_START_RE.search(command or ""):
            self.server_started = True

    def add_artifacts(self, artifacts: list[str]) -> None:
        for a in artifacts:
            if a not in self.artifacts:
                self.artifacts.append(a)

    def add_pid(self, pid: str) -> None:
        if pid and pid not in self.background_pids:
            self.background_pids.append(pid)

    def record_probe_result(self, result: ProbeResult) -> None:
        """Store URL health while allowing a later success to supersede failure."""
        key = normalize_probe_url(result.advertised_url)
        previous = self._latest_probes.get(key)
        if previous is None or result.ok or not previous.ok:
            self._latest_probes[key] = result
        self.probe_results.append(result)

    def has_successful_build_command(self) -> bool:
        for c in self.commands:
            if c.get("ok") and c.get("exit_code") == 0 and _BUILD_VERBS.search(c.get("command", "")):
                return True
        return False

    def has_failed_command(self) -> bool:
        return any(not c.get("ok") and c.get("exit_code") not in (None, 0) for c in self.commands)

    def reachable_urls(self) -> list[ProbeResult]:
        probes = self._current_probes()
        return [r for r in probes if r.ok and r.status_code < 500]

    def unreachable_urls(self) -> list[ProbeResult]:
        probes = self._current_probes()
        return [r for r in probes if not r.ok]

    def _current_probes(self) -> list[ProbeResult]:
        if self._latest_probes:
            return list(self._latest_probes.values())
        # Backward-compatible support for callers that constructed state by
        # appending to probe_results before record_probe_result existed.
        return self.probe_results

    def facts_for_prompt(self) -> list[str]:
        """Concise facts to inject into the next provider request."""
        facts: list[str] = []
        if self.files_modified:
            facts.append("Files modified: " + ", ".join(sorted(self.files_modified)))
        if self.artifacts:
            facts.append("Artifacts discovered: " + ", ".join(self.artifacts))
        if self.has_successful_build_command():
            facts.append("A build/package command succeeded (exit 0)")
        for c in self.commands:
            if not c.get("ok") and c.get("exit_code") not in (None, 0):
                facts.append(f"Failed command (exit {c['exit_code']}): {c['command'][:120]}")
        for r in self.reachable_urls():
            facts.append(f"Reachable server: {r.advertised_url} (HTTP {r.status_code})")
        for r in self.unreachable_urls():
            facts.append(f"Unreachable server: {r.advertised_url} ({r.error})")
        if self.background_pids:
            facts.append("Background jobs: " + ", ".join(self.background_pids))
        return facts


@dataclass
class CompletionVerdict:
    accept: bool
    missing: list[str] = field(default_factory=list)
    nudge: str = ""


def classify_task(state: ExecutionState) -> set[str]:
    """Return the set of applicable evidence categories."""
    cats: set[str] = set()
    text = state.task or ""
    if _PACKAGE_KEYWORDS.search(text) or any(
        _BUILD_VERBS.search(c.get("command", "")) for c in state.commands
    ):
        cats.add("build")
    elif re.search(r"\b(build|as\s+an?\s+exe|\.exe|installer|package)\b", text, re.IGNORECASE):
        cats.add("build")
    if _SERVER_TASK_RE.search(text) or state.server_started:
        cats.add("server")
    if re.search(
        r"\b(implement|add|create|refactor|write|fix|build\s+a|make\s+a|develop|feature)\b",
        text, re.IGNORECASE,
    ):
        cats.add("implementation")
    return cats


def evaluate_completion(state: ExecutionState, has_text_response: bool) -> CompletionVerdict:
    """Decide whether a text-only stop should be accepted.

    A nudge is emitted at most once per category, but unresolved obligations
    never become accepted merely because they were already mentioned.
    """
    cats = classify_task(state)
    missing: list[str] = []
    nudges: list[str] = []

    def nudge_once(key: str, msg: str) -> str | None:
        if key in state.nudges_used:
            return None
        state.nudges_used.add(key)
        return msg

    if "build" in cats:
        if not state.artifacts and not state.has_successful_build_command():
            msg = nudge_once(
                "build_evidence",
                "Do not report completion yet. The task requires a built/packaged "
                "artifact (e.g. an .exe/.msi/.whl) or a successful build command "
                "(exit 0). Run the build/package step and report the produced "
                "artifact path or the successful build command output.",
            )
            missing.append("build_artifact_or_successful_build")
            if msg:
                nudges.append(msg)

    if "server" in cats and state.server_started:
        reachable = state.reachable_urls()
        unreachable = state.unreachable_urls()
        if not reachable and unreachable:
            msg = nudge_once(
                "server_reachability",
                "A dev server was started but its advertised URL was unreachable. "
                "Do not report success. Either wait longer and re-check with "
                "shell_poll, fix the server startup error, or document exactly "
                "why it cannot be reached.",
            )
            missing.append("reachable_server_url")
            if msg:
                nudges.append(msg)

    if missing:
        return CompletionVerdict(accept=False, missing=missing, nudge="\n".join(nudges))
    return CompletionVerdict(accept=True)
