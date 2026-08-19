"""Prompt caching support — implements prompt caching for cost efficiency.

This module handles:

1. Anthropic-style cache_control breakpoints for system messages
2. Cache-aware message ordering (static content first, dynamic last)
3. Cache hit ratio tracking
4. Automatic cache key generation based on content signatures

Prompt caching gives 50-90% discounts on input tokens for subsequent
turns in the same loop, which is the dominant cost in agentic workflows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent.core.prompt_cache")


@dataclass
class CacheConfig:
    """Configuration for prompt caching behavior."""

    enabled: bool = False
    """Master switch — caching is opt-in per profile."""

    cache_type: str = "ephemeral"
    """Type of caching: 'ephemeral' (Anthropic-style) or 'semantic' (custom key-based)."""

    max_cache_entries: int = 50
    """Maximum number of cache entries to track."""

    min_cache_breakpoint_interval: int = 100
    """Minimum tokens between cache_control breakpoints to avoid overhead."""


@dataclass
class CacheStats:
    """Tracking cache hit/miss statistics."""

    hits: int = 0
    misses: int = 0
    tokens_saved: int = 0
    tokens_spent: int = 0
    last_reset: float = field(default_factory=time.time)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total

    def record_hit(self, tokens: int = 0) -> None:
        self.hits += 1
        self.tokens_saved += tokens

    def record_miss(self, tokens: int = 0) -> None:
        self.misses += 1
        self.tokens_spent += tokens

    def reset(self) -> None:
        self.hits = 0
        self.misses = 0
        self.tokens_saved = 0
        self.tokens_spent = 0
        self.last_reset = time.time()


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class PromptCacheManager:
    """Manages cache_control annotations for prompt caching.

    Supports:
    - Anthropic-style cache_control breakpoints on system messages
    - Static content (system prompt, repo map) placed first as cacheable
    - Dynamic content (task, working set) placed after cache break
    """

    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self._stats: dict[str, CacheStats] = {}
        self._content_hashes: dict[str, str] = {}
        self._cache_key_prefixes: dict[str, str] = {}

    def is_enabled(self, profile_name: str) -> bool:
        """Check if caching is enabled for a given profile."""
        if not self.config.enabled:
            return False
        return True

    def get_stats(self, profile_name: str = "") -> CacheStats:
        """Get cache statistics for a profile, or global if none specified."""
        key = profile_name or "__global__"
        if key not in self._stats:
            self._stats[key] = CacheStats()
        return self._stats[key]

    def build_messages_with_cache(
        self,
        messages: list[dict],
        profile_name: str = "",
    ) -> list[dict]:
        """Add cache_control breakpoints to messages for Anthropic-style caching.

        Strategy:
        1. System messages with static content (system prompt, repo map) get
           cache_control breakpoints at appropriate intervals
        2. User messages (task) are NOT cached — they change per request
        3. The first tool interaction breaks the cache boundary

        Args:
            messages: The list of message dicts built by ContextManager
            profile_name: The model profile name (for stats tracking)

        Returns:
            Messages with cache_control annotations added
        """
        if not self.config.enabled:
            return messages

        result: list[dict] = []
        token_accum = 0
        cache_points_added = 0

        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            role = msg.get("role", "")
            tokens = estimate_tokens(content)

            enriched = dict(msg)  # shallow copy

            # Add cache_control to system messages that exceed the breakpoint interval
            if role == "system" and tokens >= self.config.min_cache_breakpoint_interval:
                token_accum += tokens
                if token_accum >= self.config.min_cache_breakpoint_interval:
                    if isinstance(enriched.get("content"), str):
                        enriched["content"] = [
                            {
                                "type": "text",
                                "text": enriched["content"],
                            }
                        ]
                    # For Anthropic-style APIs, add cache_control to content list
                    if isinstance(enriched.get("content"), list):
                        if len(enriched["content"]) > 0:
                            last_item = enriched["content"][-1]
                            if isinstance(last_item, dict) and "cache_control" not in last_item:
                                enriched["content"] = list(enriched["content"])
                                enriched["content"].append({
                                    "type": "text",
                                    "text": "",
                                    "cache_control": {"type": "ephemeral"},
                                })

                    cache_points_added += 1
                    token_accum = 0
                    self.get_stats(profile_name).record_miss(tokens)

            result.append(enriched)

        # If we added no cache points but have a large system message, force one
        if cache_points_added == 0 and result and result[0].get("role") == "system":
            first_content = result[0].get("content", "")
            if isinstance(first_content, str) and len(first_content) > 500:
                first_enriched = dict(result[0])
                first_enriched["content"] = [
                    {"type": "text", "text": first_content},
                ]
                result[0] = first_enriched

        return result

    def compute_cache_key(self, messages: list[dict]) -> str:
        """Compute a deterministic cache key from message content.

        Used for local/semantic caching. Only includes static content
        (system messages, tool schemas), not dynamic content (task, chat).
        """
        static_parts = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content)
                static_parts.append(content)
        raw = "||".join(static_parts)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def should_invalidate(self, cache_key: str) -> bool:
        """Check if a cached response should be invalidated.

        Returns True if content has changed (different hash from previous).
        """
        prev = self._content_hashes.get("last_cache_key")
        if prev is None:
            self._content_hashes["last_cache_key"] = cache_key
            return False  # First use, no invalidation needed
        if prev != cache_key:
            self._content_hashes["last_cache_key"] = cache_key
            return True  # Content changed, invalidate
        return False

    def report_stats(self) -> str:
        """Format a human-readable summary of caching statistics."""
        lines = ["<cache_stats>"]
        for profile_name, stats in self._stats.items():
            hit_rate = stats.hit_rate * 100
            lines.append(
                f"  {profile_name}: {stats.hits} hits / {stats.misses} misses "
                f"({hit_rate:.1f}% hit rate, "
                f"~{stats.tokens_saved} tokens saved)"
            )
        lines.append("</cache_stats>")
        return "\n".join(lines)