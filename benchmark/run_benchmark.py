from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .runner import discover_exercises, run_single_exercise
from .results import BenchmarkRun


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the Aider Exercism benchmark against our agent",
    )
    p.add_argument(
        "--exercises-dir",
        type=str,
        required=True,
        help="Path to the polyglot-benchmark directory",
    )
    p.add_argument(
        "--languages",
        nargs="+",
        default=["python", "javascript"],
        help="Languages to benchmark (default: python javascript)",
    )
    p.add_argument(
        "--tier",
        type=str,
        default="quality",
        choices=["low", "balanced", "quality"],
        help="Agent tier (default: quality)",
    )
    p.add_argument(
        "--num-tests",
        type=int,
        default=0,
        help="Limit number of exercises (0 = all)",
    )
    p.add_argument(
        "--keywords",
        nargs="*",
        default=None,
        help="Filter exercises by keyword",
    )
    p.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Number of parallel exercises (default: 1, sequential)",
    )
    p.add_argument(
        "--output",
        type=str,
        default="",
        help="Path to write JSON results file",
    )
    return p.parse_args()


async def run_benchmark(args: argparse.Namespace) -> BenchmarkRun:
    exercises = discover_exercises(
        args.exercises_dir,
        languages=args.languages,
        keywords=args.keywords,
        num_tests=args.num_tests,
    )

    print(f"Found {len(exercises)} exercises across {args.languages}")
    for lang in args.languages:
        count = sum(1 for _, l, _ in exercises if l == lang)
        print(f"  {lang}: {count} exercises")
    print(f"Tier: {args.tier}")
    print(f"Threads: {args.threads}")
    print()

    run = BenchmarkRun(started_at=datetime.now(timezone.utc).isoformat())

    if args.threads <= 1:
        for i, (name, lang, exercise_dir) in enumerate(exercises, 1):
            print(f"[{i}/{len(exercises)}] {lang}/{name} ... ", end="", flush=True)
            result = await run_single_exercise(name, lang, exercise_dir, tier=args.tier)
            run.add(result)
            status = "PASS" if result.passed else ("ERR!" if result.error else "FAIL")
            print(f"{status} ({result.time_seconds:.1f}s)")
    else:
        sem = asyncio.Semaphore(args.threads)

        async def bounded(i: int, name: str, lang: str, exercise_dir: Path):
            async with sem:
                print(f"[{i}/{len(exercises)}] {lang}/{name} ... ", end="", flush=True)
                result = await run_single_exercise(name, lang, exercise_dir, tier=args.tier)
                status = "PASS" if result.passed else ("ERR!" if result.error else "FAIL")
                print(f"{status} ({result.time_seconds:.1f}s)")
                return result

        tasks = [
            bounded(i, name, lang, exercise_dir)
            for i, (name, lang, exercise_dir) in enumerate(exercises, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                run.add(type(run.results[0] if run.results else None)(
                    exercise="unknown",
                    language="unknown",
                    passed=False,
                    time_seconds=0,
                    error=str(r),
                ))
            else:
                run.add(r)

    run.finished_at = datetime.now(timezone.utc).isoformat()
    return run


def main():
    import pathlib

    _here = pathlib.Path(__file__).resolve()
    _agent_root = _here.parent.parent
    _parent = str(_agent_root.parent)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)

    args = parse_args()

    exercises_path = Path(args.exercises_dir)
    if not exercises_path.is_dir():
        print(f"ERROR: exercises-dir not found: {args.exercises_dir}")
        sys.exit(1)

    if not args.output:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.output = f"benchmark_results_{ts}.json"

    run = asyncio.run(run_benchmark(args))

    run.print_summary()
    run.save_json(args.output)
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
