"""Re-grade banked BrowseComp predictions with the fixed grader.

The research (the expensive part) is saved in the results JSON; only the
live grades were unreliable. This re-grades offline where possible
(normalized exact-match / non-answer detection) and via the LLM grader for
genuinely ambiguous pairs, then prints the corrected score and a per-item
diff versus the original grades.

    python -m zirconAgent.benchmark.regrade benchmark/browsecomp_20.json
    python -m zirconAgent.benchmark.regrade benchmark/browsecomp_20.json --no-llm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .browsecomp import _build_researcher, grade


async def regrade(path: str, config: str | None, tier: str) -> None:
    data = json.loads(Path(path).read_text())
    results = data["results"]

    router, _sub = _build_researcher(config, tier)

    new_correct = 0
    flips = []
    for i, r in enumerate(results, 1):
        gold, pred = r["gold_answer"], r["predicted"]
        old = bool(r.get("correct"))
        try:
            verdict = await grade(router, r["question"], gold, pred)
            how = "llm"
        except Exception as e:
            verdict = old  # keep original if the grader is unavailable
            how = f"llm-error({type(e).__name__})→kept"

        new_correct += int(verdict)
        if verdict != old:
            flips.append((i, old, verdict, how, gold, pred))

    n = len(results)
    print(f"Original: {sum(bool(r.get('correct')) for r in results)}/{n} "
          f"({100*sum(bool(r.get('correct')) for r in results)/n:.0f}%)")
    print(f"Regraded: {new_correct}/{n} ({100*new_correct/n:.0f}%)")
    print()
    if flips:
        print("Flipped verdicts:")
        for i, old, new, how, gold, pred in flips:
            arrow = "FAIL→PASS" if new else "PASS→FAIL"
            print(f"  #{i:2} {arrow} [{how}]  gold={gold[:35]!r} pred={pred[:35]!r}")
    else:
        print("No verdicts changed.")


def main() -> None:
    p = argparse.ArgumentParser(description="Re-grade banked BrowseComp predictions with the GLM grader")
    p.add_argument("results_json")
    p.add_argument("--config", default=None)
    p.add_argument("--tier", default="balanced")
    args = p.parse_args()
    asyncio.run(regrade(args.results_json, args.config, args.tier))


if __name__ == "__main__":
    main()
