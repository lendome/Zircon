from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator


@dataclass
class ExerciseResult:
    exercise: str
    language: str
    passed: bool
    time_seconds: float
    test_output: str = ""
    agent_answer: str = ""
    error: str = ""


@dataclass
class BenchmarkStats:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float = 0.0
    avg_time: float = 0.0
    total_time: float = 0.0


@dataclass
class BenchmarkRun:
    results: list[ExerciseResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def add(self, result: ExerciseResult) -> None:
        self.results.append(result)

    def stats(self, language: str | None = None) -> BenchmarkStats:
        rows = self.results
        if language:
            rows = [r for r in rows if r.language == language]

        total = len(rows)
        if total == 0:
            return BenchmarkStats()

        passed = sum(1 for r in rows if r.passed)
        failed = sum(1 for r in rows if not r.passed and not r.error)
        errors = sum(1 for r in rows if r.error)
        total_time = sum(r.time_seconds for r in rows)

        return BenchmarkStats(
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            pass_rate=passed / total if total else 0.0,
            avg_time=total_time / total if total else 0.0,
            total_time=total_time,
        )

    def languages(self) -> list[str]:
        return sorted(set(r.language for r in self.results))

    def print_summary(self) -> None:
        overall = self.stats()
        print("\n" + "=" * 60)
        print("  BENCHMARK RESULTS")
        print("=" * 60)
        print(f"  Started:   {self.started_at}")
        print(f"  Finished:  {self.finished_at}")
        print(f"  Total:     {overall.total} exercises")
        print(f"  Passed:    {overall.passed}")
        print(f"  Failed:    {overall.failed}")
        print(f"  Errors:    {overall.errors}")
        print(f"  Pass Rate: {overall.pass_rate:.1%}")
        print(f"  Avg Time:  {overall.avg_time:.1f}s")
        print(f"  Total Time: {overall.total_time:.1f}s")

        for lang in self.languages():
            s = self.stats(lang)
            print(f"\n  [{lang.upper()}] {s.passed}/{s.total} passed ({s.pass_rate:.1%}) | avg {s.avg_time:.1f}s")

        print("\n" + "-" * 60)
        print("  PER-EXERCISE RESULTS")
        print("-" * 60)
        for r in self.results:
            status = "PASS" if r.passed else ("ERR!" if r.error else "FAIL")
            print(f"  [{status}] {r.language}/{r.exercise} ({r.time_seconds:.1f}s)")
            if not r.passed and r.test_output:
                for line in r.test_output.splitlines()[:3]:
                    print(f"         {line}")
        print("=" * 60)

    def save_json(self, path: str | Path) -> None:
        data = {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stats": {
                "overall": asdict(self.stats()),
                "by_language": {lang: asdict(self.stats(lang)) for lang in self.languages()},
            },
            "results": [asdict(r) for r in self.results],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str))
