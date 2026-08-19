"""Compare RapidAPI SERP providers on the same query, side by side.

    python -m zirconAgent.benchmark.compare_search "your query here"
    python -m zirconAgent.benchmark.compare_search "query" --n 8
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from ..tools.web_ops import WebSearchTool

# The RapidAPI SERP hosts to compare. Add more here.
_PROVIDERS = [
    "google-api31.p.rapidapi.com",
    "google-search116.p.rapidapi.com",
]


async def _run_one(host: str, key: str, query: str, n: int) -> dict:
    tool = WebSearchTool({"rapidapi_key": key, "rapidapi_host": host})
    t0 = time.monotonic()
    out = await tool.run(query, max_results=n)
    dt = time.monotonic() - t0
    # Count result blocks (numbered lines) + collect the URLs
    urls = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")]
    n_results = sum(1 for ln in out.splitlines() if ln.strip()[:2].rstrip(".").isdigit())
    return {"host": host, "seconds": round(dt, 2), "n_results": n_results,
            "urls": urls, "raw": out}


async def main(query: str, n: int, key: str) -> None:
    print(f'QUERY: {query!r}\n')
    results = await asyncio.gather(*[_run_one(h, key, query, n) for h in _PROVIDERS])

    for r in results:
        print("=" * 70)
        print(f"{r['host']}   ({r['seconds']}s, {r['n_results']} results)")
        print("-" * 70)
        print(r["raw"][:1400])
        print()

    # Overlap summary — how similar are the top results?
    print("=" * 70)
    print("OVERLAP (top URLs shared across providers):")
    url_sets = {r["host"]: set(u.split("?")[0] for u in r["urls"][:n]) for r in results}
    if len(url_sets) >= 2:
        hosts = list(url_sets)
        common = set.intersection(*url_sets.values())
        print(f"  shared by all: {len(common)}")
        for u in sorted(common):
            print(f"    {u}")
        for h in hosts:
            uniq = url_sets[h] - common
            print(f"  only in {h}: {len(uniq)}")


def cli() -> None:
    p = argparse.ArgumentParser(description="Compare RapidAPI SERP providers")
    p.add_argument("query")
    p.add_argument("--n", type=int, default=6, help="results per provider")
    p.add_argument("--key", default=os.environ.get("RAPIDAPI_KEY", ""))
    args = p.parse_args()
    if not args.key:
        # Fall back to the key configured in models.yaml
        from ..core.config import load_config
        _, ac = load_config()
        args.key = (ac.web_search or {}).get("rapidapi_key", "")
    asyncio.run(main(args.query, args.n, args.key))


if __name__ == "__main__":
    cli()
