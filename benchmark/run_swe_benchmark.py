from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import datetime, timezone

from .swe_runner import (
    SWEBenchmarkRun,
    evaluate_with_harness,
    load_swe_tasks,
    run_single_swe_task,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run SWE-bench Pro benchmark against our agent",
    )
    p.add_argument(
        "--dataset",
        type=str,
        default="princeton-nlp/SWE-bench_Verified",
        help="SWE-bench dataset name on HuggingFace (default: princeton-nlp/SWE-bench_Verified)",
    )
    p.add_argument(
        "--split",
        type=str,
        default="test",
        choices=["dev", "test", "train"],
        help="Dataset split (default: test)",
    )
    p.add_argument(
        "--instance-ids",
        nargs="+",
        default=None,
        help="Specific instance IDs to run (default: all in split)",
    )
    p.add_argument(
        "--num-tasks",
        type=int,
        default=0,
        help="Limit number of tasks (0 = all)",
    )
    p.add_argument(
        "--tier",
        type=str,
        default="quality",
        choices=["low", "balanced", "quality"],
        help="Agent tier (default: quality)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-task timeout in seconds (default: 600)",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Number of parallel agent tasks (default: 1, sequential)",
    )
    p.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to write JSON results file",
    )
    p.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="Skip Docker-based evaluation (generate patches only)",
    )
    p.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Docker evaluation workers (default: 4)",
    )
    p.add_argument(
        "--cache-level",
        type=str,
        default="env",
        choices=["none", "base", "env", "instance"],
        help="Docker cache level (default: env)",
    )
    return p.parse_args()


async def run_swe_benchmark(args: argparse.Namespace) -> SWEBenchmarkRun:
    tasks = load_swe_tasks(
        dataset_name=args.dataset,
        split=args.split,
        instance_ids=args.instance_ids,
        num_tasks=args.num_tasks,
    )

    print(f"Loaded {len(tasks)} tasks from {args.dataset} ({args.split})")
    print(f"Tier: {args.tier}, Timeout: {args.timeout}s")
    print(f"Threads: {args.threads}")
    if args.skip_evaluation:
        print("Evaluation: skipped (--skip-evaluation)")
    print()

    run = SWEBenchmarkRun(
        started_at=datetime.now(timezone.utc).isoformat(),
        dataset_name=args.dataset,
        split=args.split,
        tier=args.tier,
    )

    if args.threads <= 1:
        for i, task in enumerate(tasks, 1):
            iid = task["instance_id"]
            print(f"[{i}/{len(tasks)}] {iid} ... ", end="", flush=True)
            result = await run_single_swe_task(task, tier=args.tier, timeout=args.timeout)
            run.add(result)
            status = "RESOLVED" if result.resolved else ("ERR!" if result.error else "FAIL")
            print(f"{status} ({result.time_seconds:.1f}s)")
    else:
        sem = asyncio.Semaphore(args.threads)

        async def bounded(i: int, task: dict):
            async with sem:
                iid = task["instance_id"]
                print(f"[{i}/{len(tasks)}] {iid} ... ", end="", flush=True)
                result = await run_single_swe_task(task, tier=args.tier, timeout=args.timeout)
                status = "RESOLVED" if result.resolved else ("ERR!" if result.error else "FAIL")
                print(f"{status} ({result.time_seconds:.1f}s)")
                return result

        coros = [bounded(i, t) for i, t in enumerate(tasks, 1)]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                print(f"Task error: {r}")
            else:
                run.add(r)

    run.finished_at = datetime.now(timezone.utc).isoformat()

    if not args.skip_evaluation:
        print("\nRunning SWE-bench evaluation harness...")
        try:
            eval_results = evaluate_with_harness(
                run,
                max_workers=args.max_workers,
                cache_level=args.cache_level,
            )
            print(f"Evaluation complete: {run.stats().resolved}/{run.stats().total} resolved")
        except Exception as e:
            print(f"Evaluation failed: {e}")
            print("Patches were generated but not evaluated. Re-run without --skip-evaluation.")

    return run


def main():
    _here = pathlib.Path(__file__).resolve()
    _agent_root = _here.parent.parent
    _parent = str(_agent_root.parent)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)

    args = parse_args()

    if not args.output:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.output = f"swe_benchmark_results_{ts}.json"

    run = asyncio.run(run_swe_benchmark(args))

    run.print_summary()
    run.save_json(args.output)
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
