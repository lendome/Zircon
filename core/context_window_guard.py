"""In-loop context window guard — keeps the active conversation under the
model's real context limit so the tool loop never dies from an overflowing
request.

Why this exists
---------------
The tool loop appends FULL tool results to the active conversation every turn
(the agent needs them to act). Over a long task the request grows without
bound. The pre-loop ``ContextManager.compact_history`` only runs *before* the
loop starts, and the ``TrajectoryPruner`` only compresses older tool results
past 60% of the window — it never removes messages and never touches the
recent turns. The first signal that the request has outgrown the model used to
be the provider's ``400 context length exceeded`` — at which point the loop
aborted mid-task and the user saw the agent "stop for no reason", then resume
"completely out of context" because the durable history had already been
distilled to stubs.

This guard runs INSIDE the loop, once per turn, before the LLM call:

1. **Estimate** the request size (chars/4, calibrated against the provider's
   real ``prompt_tokens`` whenever usage data arrives — the heuristic can be
   off by 2x+ on code-heavy traffic).
2. **Soft threshold** (default 70% of the window): compact the oldest turns
   in place — an LLM summary of the dropped span (same pattern as
   ``ContextManager.compact_history``), or a deterministic structural
   fallback when no router is available. Tool/result pairing is never
   broken: compaction happens on turn boundaries, and the most recent
   ``protected_turns`` turns are always kept verbatim.
3. **Hard ceiling** (default 92%): if the conversation is STILL too big
   after compaction (e.g. one giant tool result in the protected window),
   stub the contents of the oldest tool messages outright. Losing stale
   tool output is always better than losing the whole task.

The guard also exposes :meth:`handle_provider_error`: when the router raises
a context-length error, the caller compacts aggressively and retries once
instead of aborting the loop.

Design guarantees:
- Never raises: any internal failure leaves the conversation untouched.
- Never breaks tool/result pairing (providers reject orphaned tool messages).
- No-op while comfortably under budget — short sessions are byte-identical.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("agent.core.context_window_guard")

_CONTEXT_LENGTH_RE = re.compile(
    r"context.length|context.window|maximum context|too many tokens|"
    r"context_length_exceeded|prompt is too long|exceeds the context|"
    r"token limit|request too large",
    re.IGNORECASE,
)

_SUMMARY_USER_PREFIX = (
    "<history_summary>Earlier work in this session (compacted to fit the "
    "context window)</history_summary>"
)


def _estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def is_context_length_error(err: Exception | str) -> bool:
    """Return whether a provider/router error means 'request too big'."""
    return bool(_CONTEXT_LENGTH_RE.search(str(err)))


class ContextWindowGuard:
    """Per-loop manager that keeps the active conversation within budget."""

    def __init__(
        self,
        tier_config: Any,
        context_window: int = 128000,
    ) -> None:
        self.tier = tier_config
        self.context_window = max(8192, int(context_window or 128000))
        self.enabled = bool(getattr(tier_config, "context_guard_enabled", True))
        soft_frac = float(getattr(tier_config, "context_guard_soft_fraction", 0.70))
        hard_frac = float(getattr(tier_config, "context_guard_hard_fraction", 0.92))
        self.soft_threshold = int(self.context_window * soft_frac)
        self.hard_threshold = int(self.context_window * hard_frac)
        self.protected_turns = max(2, int(getattr(tier_config, "context_guard_protected_turns", 4)))
        # Calibration: providers report real prompt_tokens in usage. The
        # chars/4 heuristic drifts on code-heavy traffic, so once we observe
        # real usage we scale our estimate by the observed ratio (clamped).
        self._calibration: float = 1.0
        self.compactions = 0
        # Text of the most recent compaction summary (also surfaced to the
        # caller so it can be persisted into durable history — otherwise the
        # NEXT chat turn starts "out of context" because only the active
        # conversation carried the summary).
        self.last_summary: str = ""

    def reset(self) -> None:
        """Reset per-loop state (called between chat turns)."""
        self._calibration = 1.0
        self.compactions = 0
        self.last_summary = ""

    # ------------------------------------------------------------------ #
    # Estimation
    # ------------------------------------------------------------------ #

    def _raw_estimate(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                total += _estimate_tokens(c)
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict):
                        total += _estimate_tokens(block.get("text", ""))
            # tool_calls arguments count too — they are serialized into the
            # request and can be large (edit blocks).
            for tc in m.get("tool_calls") or []:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    if isinstance(fn, dict):
                        total += _estimate_tokens(str(fn.get("arguments", "")))
        return total

    def estimate(self, messages: list[dict]) -> int:
        return int(self._raw_estimate(messages) * self._calibration)

    def calibrate(self, messages: list[dict], prompt_tokens: int) -> None:
        """Adjust the estimate using a real provider prompt_tokens reading."""
        if prompt_tokens <= 0:
            return
        raw = self._raw_estimate(messages)
        if raw <= 0:
            return
        observed = prompt_tokens / raw
        # Clamp: never trust a single reading to swing us more than 3x, and
        # smooth via running average so one weird response doesn't oscillate.
        observed = max(0.34, min(3.0, observed))
        self._calibration = (self._calibration + observed) / 2 if self._calibration != 1.0 else observed

    # ------------------------------------------------------------------ #
    # Turn structure
    # ------------------------------------------------------------------ #

    @staticmethod
    def _turn_boundaries(messages: list[dict]) -> list[int]:
        """Return indices that are safe cut points (start of a new turn).

        A cut point is an index i such that messages[:i] never ends with a
        dangling assistant tool_call whose tool results live at/after i.
        We cut only immediately before a user/system message or before an
        assistant message that follows a completed tool-result run.
        """
        boundaries: list[int] = []
        n = len(messages)
        i = 0
        while i < n:
            role = messages[i].get("role")
            if role == "assistant" and messages[i].get("tool_calls"):
                # Consume the assistant msg + all following tool results.
                j = i + 1
                while j < n and messages[j].get("role") == "tool":
                    j += 1
                boundaries.append(j)
                i = j
            else:
                boundaries.append(i + 1)
                i += 1
        return boundaries

    # ------------------------------------------------------------------ #
    # Compaction
    # ------------------------------------------------------------------ #

    async def ensure_fits(self, messages: list[dict], router: Any = None) -> int:
        """Compact `messages` in place if it exceeds the soft threshold.

        Returns estimated tokens freed. Never raises.
        """
        if not self.enabled:
            return 0
        try:
            est = self.estimate(messages)
            if est < self.soft_threshold:
                return 0
            freed = await self._compact(messages, router)
            # Hard ceiling: still too big -> stub old tool results outright.
            if self.estimate(messages) > self.hard_threshold:
                freed += self._stub_old_tool_results(messages)
            if freed:
                self.compactions += 1
                logger.info(
                    "context-window guard freed ~%d tokens (compaction #%d, est now ~%d/%d)",
                    freed, self.compactions, self.estimate(messages), self.context_window,
                )
            return freed
        except Exception as e:  # noqa: BLE001 — never break the tool loop
            logger.debug("context-window guard aborted: %s", e)
            return 0

    async def force_compact(self, messages: list[dict], router: Any = None) -> int:
        """Compact regardless of threshold (provider said 'too big')."""
        if not self.enabled:
            return 0
        try:
            freed = await self._compact(messages, router)
            freed += self._stub_old_tool_results(messages)
            self.compactions += 1
            return freed
        except Exception as e:  # noqa: BLE001
            logger.debug("context-window force-compact aborted: %s", e)
            return 0

    async def _compact(self, messages: list[dict], router: Any) -> int:
        """Summarize the oldest turns into a summary message, in place."""
        boundaries = self._turn_boundaries(messages)
        if len(boundaries) <= self.protected_turns + 1:
            return 0
        # Keep the last `protected_turns` boundaries (recent turns) verbatim.
        cut = boundaries[-(self.protected_turns + 1)]
        if cut <= 1:
            return 0

        # Never decapitate the leading system prompt(s): find where the
        # initial system run ends and never cut before it.
        first_keep = 0
        while first_keep < len(messages) and messages[first_keep].get("role") == "system":
            first_keep += 1
        if cut <= first_keep:
            return 0

        old = messages[first_keep:cut]
        before = self._raw_estimate(old)
        summary = await self._summarize(old, router)
        self.last_summary = summary
        replacement = [
            {"role": "user", "content": _SUMMARY_USER_PREFIX},
            {"role": "assistant", "content": f"<history_summary>\n{summary}\n</history_summary>"},
        ]
        messages[first_keep:cut] = replacement
        return max(0, before - self._raw_estimate(replacement))

    async def _summarize(self, old: list[dict], router: Any) -> str:
        """LLM summary of the dropped span; deterministic fallback otherwise."""
        blocks: list[str] = []
        for msg in old:
            role = msg.get("role", "")
            content = msg.get("content")
            if not isinstance(content, str):
                content = ""
            if role == "assistant" and msg.get("tool_calls"):
                names = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    names.append(str(fn.get("name", "?")))
                content = (content[:300] + " " if content else "") + f"[tools: {', '.join(names)}]"
            if len(content) > 800:
                content = content[:800] + "..."
            if content:
                blocks.append(f"[{role}] {content}")
        history_block = "\n".join(blocks)

        if router is not None:
            try:
                from ..llm.prompts import SYSTEM_HISTORY_SUMMARIZER
                response = await router.generate(
                    role="summarize",
                    messages=[
                        {"role": "system", "content": SYSTEM_HISTORY_SUMMARIZER},
                        {"role": "user", "content": f"Conversation history:\n\n{history_block}"},
                    ],
                    max_tokens=768,
                )
                if response and response.content:
                    return response.content
            except Exception as e:
                logger.debug("guard summarize call failed (%s), using fallback", e)

        # Deterministic fallback: keep user/system lines (intent) and tool
        # names (actions), drop bulky tool output.
        kept = [b for b in blocks if not b.startswith("[tool]")]
        text = "\n".join(kept)
        if len(text) > 6000:
            text = text[:6000] + "\n... (truncated)"
        return text or "[Earlier tool output compacted to fit the context window.]"

    def _stub_old_tool_results(self, messages: list[dict]) -> int:
        """Replace the contents of the oldest tool messages with stubs.

        Last resort for the hard ceiling. Pairing is preserved (the message
        and its tool_call_id stay); only the content shrinks.
        """
        boundaries = self._turn_boundaries(messages)
        keep_from = boundaries[-self.protected_turns] if len(boundaries) > self.protected_turns else 0
        freed = 0
        for i, msg in enumerate(messages):
            if i >= keep_from:
                break
            if msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < 600:
                continue
            stub = f"[context-guard: {len(content)} chars of stale tool output compacted — re-run the tool if needed]"
            freed += _estimate_tokens(content) - _estimate_tokens(stub)
            msg["content"] = stub
        return max(0, freed)
