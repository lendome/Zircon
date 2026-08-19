"""Trajectory reduction (AgentDiet) — real-time pruning of the active tool loop.

The *active* conversation passed to the provider grows turn over turn. Without
pruning it eventually swamps the context window with low-value content:
verbose build logs, echoed edit blocks, and the full contents of files that
were only read while searching and are no longer relevant.

This module compresses OLDER tool-result messages in place, in three ways:

1. **Useless** — collapse noisy build/compile output (make enter/leave dir
   lines, ``__pycache__`` creation spam, progress bars) in tool results older
   than the protected window.
2. **Redundant** — replace edit tool responses that echo the entire replaced
   block with a one-line delta summary (the model already knows it made the
   edit; the echo is redundant).
3. **Expired** — once the agent has moved past a read-only exploration (the
   file was read while searching but never edited), the full contents of those
   earlier reads are replaced with a compact stub. The file can be re-read
   with ``read_file`` if genuinely needed again.

Design guarantees (unobtrusive even in edge cases):
- When the trajectory is comfortably below the budget threshold, NOTHING is
  changed — the active conversation stays fully intact.
- The most recent ``protected_turns`` turns are never touched, so the model
  always has its freshest tool results verbatim.
- Tool/result pairing is never broken: only tool message *content* is
  rewritten. Messages are never removed and ``tool_call_id`` is preserved.
- Non-string / malformed / orphaned tool messages are skipped silently.
- Any exception aborts pruning and leaves the original trajectory untouched.
- Pruning never touches assistant messages (which carry ``tool_calls``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("agent.core.trajectory_diet")

_READ_TOOLS = frozenset({
    "read_file", "grep_code", "find_symbols", "get_structure",
    "glob_files", "list_dir",
})
_EDIT_TOOLS = frozenset({
    "edit_file", "edit_lines", "create_file", "delete_file", "aider_edit",
})
_SHELL_TOOLS = frozenset({
    "run_command", "shell_start", "shell_poll", "shell_stop",
    "shell_input", "shell_close_stdin",
    "run_in_terminal", "terminal_output", "terminal_stop",
})

# Lines that are pure build/log noise — safe to drop wholesale.
_NOISE_PATTERNS = [
    re.compile(r"^\s*make\[\d+\]:", re.IGNORECASE),
    re.compile(r"__pycache__", re.IGNORECASE),
    re.compile(r"^\s*Creating directory\b", re.IGNORECASE),
    re.compile(r"^\s*\[\s*\d{1,3}%\s*\]"),
    re.compile(r"^\s*(mkdir|created|creating)\b.*__pycache__", re.IGNORECASE),
    re.compile(r"^\s*(g?cc|clang|rustc|cargo|tsc|vite|webpack):\s*(warning|note|info)\b", re.IGNORECASE),
]

_EXPIRED_STUB = "[trajectory-diet: expired read of {label} ({n} chars) — re-read with read_file if needed]"
_EDIT_STUB_NOTE = "[trajectory-diet: {n} chars of echoed edit block compressed]"


def _estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _paths_from_args(args: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("path", "file_path", "cwd"):
        v = args.get(key)
        if isinstance(v, str) and v:
            paths.append(v)
    return paths


class TrajectoryPruner:
    """Compress older tool-result messages in the active conversation.

    The pruner is invoked once per tool turn. It is a no-op until the
    estimated token size of the active conversation exceeds a threshold
    (a fraction of the model context window), so it never alters a
    comfortably-sized trajectory.
    """

    def __init__(
        self,
        tier_config: Any,
        context_window: int = 32000,
    ) -> None:
        self.tier = tier_config
        self.context_window = max(4096, int(context_window))
        self.enabled = bool(getattr(tier_config, "trajectory_pruning_enabled", True))
        frac = getattr(tier_config, "trajectory_prune_threshold_fraction", 0.6)
        self.threshold = max(8000, int(self.context_window * float(frac)))
        self.protected_turns = max(1, int(getattr(tier_config, "trajectory_protected_turns", 3)))
        self.min_messages = max(6, int(getattr(tier_config, "trajectory_min_messages", 8)))
        self._passes = 0
        self._total_saved = 0

    def reset(self) -> None:
        self._passes = 0
        self._total_saved = 0

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #

    def maybe_prune(self, messages: list[dict]) -> int:
        """Compress older tool results if the trajectory is over budget.

        Returns the estimated number of tokens freed. Always safe to call;
        any internal failure is swallowed so the tool loop is never broken.
        """
        if not self.enabled or len(messages) < self.min_messages:
            return 0
        if self._estimate_total(messages) < self.threshold:
            return 0
        try:
            saved = self._prune(messages, aggressive=False)
            # If we are still well over budget after a conservative pass,
            # run one more aggressive pass to keep the loop healthy.
            if saved > 0 and self._estimate_total(messages) > self.threshold:
                saved += self._prune(messages, aggressive=True)
            self._passes += 1
            self._total_saved += saved
            if saved:
                logger.debug(
                    "trajectory-diet freed ~%d tokens (pass %d, total ~%d)",
                    saved, self._passes, self._total_saved,
                )
            return saved
        except Exception as e:  # noqa: BLE001 — never break the tool loop
            logger.debug("trajectory prune aborted: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _estimate_total(messages: list[dict]) -> int:
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += _estimate_tokens(c)
            elif isinstance(c, list):
                # Anthropic-style content blocks — sum text parts only.
                for block in c:
                    if isinstance(block, dict):
                        total += _estimate_tokens(block.get("text", ""))
        return total

    def _build_turns(self, messages: list[dict]) -> list[dict]:
        """Group messages into turns: one assistant(tool_calls) + its tool msgs."""
        turns: list[dict] = []
        pending: list[int] | None = None
        for i, m in enumerate(messages):
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                turns.append({"assistant": i, "tools": []})
                pending = turns[-1]["tools"]
            elif role == "tool":
                if pending is not None:
                    pending.append(i)
                # Orphan tool message (no preceding assistant tool_call) — skip.
        return turns

    @staticmethod
    def _build_call_index(messages: list[dict]) -> dict[str, dict[str, Any]]:
        idx: dict[str, dict[str, Any]] = {}
        for m in messages:
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                tid = tc.get("id")
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments")
                args: dict[str, Any] = {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                if tid:
                    idx[tid] = {"name": name, "args": args}
        return idx

    @staticmethod
    def _collect_files(
        messages: list[dict], call_index: dict[str, dict[str, Any]]
    ) -> tuple[set[str], set[str]]:
        read_files: set[str] = set()
        edited_files: set[str] = set()
        for m in messages:
            if m.get("role") != "tool":
                continue
            info = call_index.get(m.get("tool_call_id"))
            if not info:
                continue
            name = info["name"]
            paths = _paths_from_args(info["args"])
            if name in _READ_TOOLS:
                read_files.update(paths)
            elif name in _EDIT_TOOLS:
                edited_files.update(paths)
        return read_files, edited_files

    def _prune(self, messages: list[dict], aggressive: bool) -> int:
        turns = self._build_turns(messages)
        if len(turns) <= self.protected_turns:
            return 0

        call_index = self._build_call_index(messages)
        read_files, edited_files = self._collect_files(messages, call_index)
        # Expired = read while exploring but never edited afterwards.
        expired = read_files - edited_files

        # Indices belonging to the protected (most recent) turns — never prune.
        protected: set[int] = set()
        for t in turns[-self.protected_turns:]:
            protected.add(t["assistant"])
            protected.update(t["tools"])

        saved = 0
        for t in turns[:-self.protected_turns]:
            for ti in t["tools"]:
                if ti in protected:  # defensive — should never be true here
                    continue
                saved += self._compress_tool(
                    messages[ti], call_index, expired, aggressive
                )
        return saved

    def _compress_tool(
        self,
        msg: dict,
        call_index: dict[str, dict[str, Any]],
        expired: set[str],
        aggressive: bool,
    ) -> int:
        content = msg.get("content")
        if not isinstance(content, str) or not content:
            return 0
        info = call_index.get(msg.get("tool_call_id"))
        if not info:
            return 0
        name = info["name"]
        paths = _paths_from_args(info["args"])
        original = _estimate_tokens(content)

        new_content: str | None = None
        if name in _EDIT_TOOLS:
            new_content = self._compress_edit(content, paths)
        elif name in _READ_TOOLS:
            new_content = self._compress_read(content, paths, expired, aggressive)
        elif name in _SHELL_TOOLS:
            new_content = self._compress_noisy(content, aggressive)

        if new_content is None or new_content == content:
            return 0
        if len(new_content) >= len(content):
            return 0
        msg["content"] = new_content
        return original - _estimate_tokens(new_content)

    # -- strategies ----------------------------------------------------- #

    @staticmethod
    def _compress_edit(content: str, paths: list[str]) -> str | None:
        # Keep genuine error/failure results intact — the model needs them.
        stripped = content.lstrip()
        if stripped.startswith((
            "Error", "Edit failed", "FAIL:", "Cannot", "No match",
            "SyntaxError", "Validation",
        )):
            return None
        if len(content) < 400:
            return None  # already compact
        label = paths[0] if paths else "file"
        first = content.splitlines()[0][:200] if content else ""
        return f"{first}\n{_EDIT_STUB_NOTE.format(n=len(content))}"

    @staticmethod
    def _compress_read(
        content: str,
        paths: list[str],
        expired: set[str],
        aggressive: bool,
    ) -> str | None:
        if not paths or len(content) < 800:
            return None
        is_expired = any(p in expired for p in paths)
        if is_expired:
            return _EXPIRED_STUB.format(label=paths[0], n=len(content))
        if aggressive and len(content) > 4000:
            # Still over budget: stub large non-expired reads too, but only
            # in the aggressive pass and only for very large contents.
            return _EXPIRED_STUB.format(label=paths[0], n=len(content))
        return None

    @staticmethod
    def _compress_noisy(content: str, aggressive: bool) -> str | None:
        if len(content) < 2000:
            return None
        lines = content.splitlines()
        if len(lines) < 20:
            return None
        kept = [ln for ln in lines if not any(p.search(ln) for p in _NOISE_PATTERNS)]
        removed = len(lines) - len(kept)
        if removed >= 5:
            return "\n".join(kept) + f"\n[trajectory-diet: {removed} noisy build/log lines removed]"
        if aggressive and len(content) > 6000:
            # Head/tail compression for very large stale shell output.
            head = lines[:8]
            tail = lines[-8:]
            mid = len(lines) - 16
            return (
                "\n".join(head)
                + f"\n... [trajectory-diet: {mid} intermediate lines collapsed] ...\n"
                + "\n".join(tail)
            )
        return None
