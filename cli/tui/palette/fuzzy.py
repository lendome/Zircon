"""
Fuzzy search — subsequence matching with scoring.

Scores how well a query matches a target string. Higher = better match.
Supports case-insensitive matching, consecutive character bonuses,
and word-boundary bonuses for better ranking.
"""

from __future__ import annotations


def fuzzy_score(query: str, target: str) -> float:
    """Score a query against a target. Higher = better. 0 = no match.

    Uses subsequence matching with bonuses for:
    - Consecutive characters (compaction)
    - Word boundaries (start of word, after separator)
    - Case match
    - Matching earlier in the target
    """
    if not query:
        return 1.0
    if not target:
        return 0.0

    q = query.lower()
    t = target.lower()

    if q not in t and not _is_subsequence(q, t):
        return 0.0

    score = 0.0
    qi = 0
    consecutive = 0
    last_match = -1

    for ti, ch in enumerate(t):
        if qi >= len(q):
            break
        if ch == q[qi]:
            # Base score for matching
            score += 1.0

            # Bonus for matching at word boundaries
            if ti == 0 or target[ti - 1] in (" ", ".", "_", "-", "/"):
                score += 0.5
            elif target[ti].isupper() and not target[ti - 1].isupper():
                score += 0.3

            # Bonus for case match
            if target[ti] == query[qi]:
                score += 0.1

            # Consecutive character bonus
            if last_match == ti - 1:
                consecutive += 1
                score += consecutive * 0.2
            else:
                consecutive = 0

            # Earlier match bonus
            score += max(0, (len(t) - ti)) * 0.01

            last_match = ti
            qi += 1

    if qi < len(q):
        return 0.0

    # Normalize by target length (shorter targets that match are better)
    score /= len(t) * 0.5 + 0.5

    return score


def fuzzy_rank(query: str, targets: list[str]) -> list[tuple[str, float]]:
    """Rank targets by fuzzy score. Returns sorted (target, score) list."""
    scored = [(t, fuzzy_score(query, t)) for t in targets]
    scored = [(t, s) for t, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _is_subsequence(query: str, target: str) -> bool:
    """Check if query is a subsequence of target."""
    qi = 0
    for ch in target:
        if qi < len(query) and ch == query[qi]:
            qi += 1
    return qi >= len(query)
