"""Hard limits for content entering provider context."""

from __future__ import annotations

from typing import Any

MAX_INGRESS_TOKENS = 8_000
TRUNCATED_PREVIEW_TOKENS = 1_000
# Fraction of the preview budget devoted to the head of the content; the
# remainder shows the tail so no part of a large result is fully invisible.
_HEAD_FRACTION = 2 / 3


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def truncate_oversized_content(
    content: str,
    *,
    source: str = "content",
    path: str = "",
) -> str:
    """Keep one huge context item from crowding out the entire request."""
    if estimate_tokens(content) <= MAX_INGRESS_TOKENS:
        return content

    preview_chars = TRUNCATED_PREVIEW_TOKENS * 4
    head_chars = int(preview_chars * _HEAD_FRACTION)
    tail_chars = preview_chars - head_chars
    target = f" `{path}`" if path else ""
    omitted_tokens = max(0, estimate_tokens(content) - TRUNCATED_PREVIEW_TOKENS)
    return (
        f"[context guard: {source}{target} was about {estimate_tokens(content):,} tokens; "
        f"showing {TRUNCATED_PREVIEW_TOKENS:,} tokens (head+tail) and omitting about "
        f"{omitted_tokens:,}. Request a narrower line range or use scrolling/navigation "
        f"tools to inspect additional portions.]\n\n"
        f"{content[:head_chars]}\n"
        f"[... omitted middle ...]\n"
        f"{content[-tail_chars:]}"
    )


def guard_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return provider-safe message copies without mutating durable history."""
    guarded: list[dict[str, Any]] = []
    for message in messages:
        copy = dict(message)
        content = copy.get("content")
        if isinstance(content, str):
            source = "tool result" if copy.get("role") == "tool" else f"{copy.get('role', 'message')} message"
            copy["content"] = truncate_oversized_content(content, source=source)
        guarded.append(copy)
    return guarded
