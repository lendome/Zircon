"""Run one research question with full tool-call tracing.

Debugging aid for research quality: dumps every tool call (name, args,
result preview) plus the final output, so we can see WHERE a research
trajectory goes wrong — bad queries, unfetched results, lost context.

Usage:
    python -m zirconAgent.benchmark.trace_question --index 0 --seed 7 --tier quality
    python -m zirconAgent.benchmark.trace_question --question "..." --tier quality
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path

from .browsecomp import QUERY_TEMPLATE, _build_researcher, fetch_dataset

_DEFAULT_OUT = Path(__file__).parent / "trace_question.json"


async def run(args: argparse.Namespace) -> None:
    if args.question:
        question, gold = args.question, "(not provided)"
    else:
        rows = await fetch_dataset()
        sample = random.Random(args.seed).sample(rows, max(args.index + 1, 3))
        question, gold = sample[args.index]["question"], sample[args.index]["answer"]

    print(f"QUESTION: {question}\nGOLD: {gold}\n", flush=True)

    router, sub = _build_researcher(args.config, args.tier)
    if getattr(args, "fast", False) and hasattr(router, "set_fast_mode"):
        router.set_fast_mode(True)
        print("Fast mode: ON (throughput routing)\n", flush=True)

    events: list[dict] = []

    def on_progress(p) -> None:
        print(f"  [{p.phase}] {p.detail[:120]}", flush=True)

    started = time.monotonic()
    task = QUERY_TEMPLATE.format(question=question)

    # Wrap the registry to trace every tool execution
    registry = sub.registry
    original_execute = registry.safe_execute

    async def traced_execute(name: str, arguments: dict, **kw) -> str:
        t0 = time.monotonic()
        result = await original_execute(name, arguments, **kw)
        t1 = time.monotonic()
        event = {
            "n": len(events) + 1,
            "tool": name,
            "args": {k: str(v)[:200] for k, v in arguments.items()},
            "result_preview": result[:400],
            "result_chars": len(result),
            "seconds": round(t1 - t0, 2),
            "start": round(t0 - started, 2),  # wall-clock offset from run start
            "end": round(t1 - started, 2),
        }
        events.append(event)
        arg_str = json.dumps(event["args"])[:150]
        print(f"  #{event['n']} {name}({arg_str}) -> {event['result_chars']} chars "
              f"in {event['seconds']}s", flush=True)
        return result

    registry.safe_execute = traced_execute  # type: ignore[method-assign]

    result = await sub.run(task, context="", progress_callback=on_progress)

    elapsed = round(time.monotonic() - started, 1)
    print(f"\nFINAL OUTPUT ({elapsed}s, {len(events)} tool calls):\n{result.output}\n")
    print(f"GOLD: {gold}")

    out = Path(args.output)
    out.write_text(json.dumps({
        "question": question,
        "gold": gold,
        "elapsed_seconds": elapsed,
        "tool_calls": events,
        "final_output": result.output,
    }, indent=2), encoding="utf-8")
    print(f"Trace written to {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="Trace a single research question")
    p.add_argument("--index", type=int, default=0, help="Question index within the sample")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--question", default="", help="Ad-hoc question instead of a dataset one")
    p.add_argument("--tier", default="quality", choices=["low", "balanced", "quality"])
    p.add_argument("--fast", action="store_true", help="Enable fast/nitro throughput routing")
    p.add_argument("--config", default=None)
    p.add_argument("--output", default=str(_DEFAULT_OUT))
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
