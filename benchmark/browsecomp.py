"""BrowseComp benchmark runner — measure web-research success rate.

BrowseComp (OpenAI, arXiv 2504.12516) is 1,266 hard-to-find, easy-to-verify
questions answered against the live web. The public dataset is XOR-encrypted
per row (keyed on a per-row canary string) to prevent training contamination;
this module implements the documented decryption scheme from openai/simple-evals.

Each question runs through Zircon's web-research pipeline (ResearcherSubAgent
with web_search / fetch_url / lookup_docs and the tier's research turn budget),
then an LLM judge grades the predicted answer against the gold answer.

Usage:
    python -m zirconAgent.benchmark.browsecomp --limit 10          # 10 sampled questions
    python -m zirconAgent.benchmark.browsecomp --limit 25 --tier quality
    python -m zirconAgent.benchmark.browsecomp --dry-run           # dataset check only

Notes:
    - Full-set runs are expensive (1,266 questions x up to research_max_turns
      LLM calls each). Use --limit with a --seed for a reproducible sample.
    - Results land in benchmark/browsecomp_results.json (or --output).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import csv
import hashlib
import io
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATASET_URL = "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
_DATA_DIR = Path(__file__).parent / "data"
_DEFAULT_OUTPUT = Path(__file__).parent / "browsecomp_results.json"

QUERY_TEMPLATE = """\
{question}

Research this on the web. Your final message MUST end with these two lines:
FINAL ANSWER: <the exact answer only — a name, title, date, or number, with no extra words>
CONFIDENCE: <0-100>"""

GRADER_TEMPLATE = """\
Judge whether the predicted answer to a question is correct, based on the gold answer.

[question]: {question}

[gold answer]: {gold_answer}

[predicted answer]: {predicted}

The predicted answer is correct if it matches the gold answer semantically —
exact wording, capitalization, and added detail do not matter, but the core
fact (the specific name/title/date/number asked for) must match without
contradiction. A predicted answer that is a reasoning fragment, a refusal, or
does not actually state the fact is NOT correct.

Call submit_verdict with correct=true or correct=false."""


# ── Dataset (decryption scheme from openai/simple-evals) ────────────────────

def derive_key(password: str, length: int) -> bytes:
    """SHA-256-derived repeating XOR key, per the simple-evals scheme."""
    key = hashlib.sha256(password.encode()).digest()
    return (key * (length // len(key) + 1))[:length]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    key = derive_key(password, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


def encrypt(plaintext: str, password: str) -> str:
    """Inverse of decrypt (XOR is symmetric); used by tests."""
    data = plaintext.encode()
    key = derive_key(password, len(data))
    return base64.b64encode(bytes(a ^ b for a, b in zip(data, key))).decode()


def load_dataset(csv_text: str) -> list[dict[str, str]]:
    """Parse and decrypt the BrowseComp CSV into question/answer rows."""
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        canary = row.get("canary", "")
        if not canary:
            continue
        try:
            rows.append({
                "question": decrypt(row.get("problem", ""), canary),
                "answer": decrypt(row.get("answer", ""), canary),
            })
        except Exception:
            continue
    return rows


async def fetch_dataset(cache_dir: Path = _DATA_DIR) -> list[dict[str, str]]:
    """Download (and cache) the encrypted dataset, return decrypted rows."""
    import httpx

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "browse_comp_test_set.csv"
    if not cache_file.is_file():
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(DATASET_URL)
            resp.raise_for_status()
            cache_file.write_text(resp.text, encoding="utf-8")
    return load_dataset(cache_file.read_text(encoding="utf-8"))


# ── Answer extraction and grading ────────────────────────────────────────────

def extract_final_answer(output: str) -> str:
    """Pull the FINAL ANSWER line out of the agent's response."""
    matches = re.findall(r"FINAL ANSWER:\s*(.+)", output or "")
    if matches:
        return matches[-1].strip()
    # Fallback: last non-empty line
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# Structured grading via a forced function call — robust against reasoning
# models that would otherwise emit prose a text parser mishandles.
_VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": "Report whether the predicted answer is correct.",
    "parameters": {
        "type": "object",
        "properties": {
            "correct": {
                "type": "boolean",
                "description": "true if the predicted answer matches the gold answer, false otherwise",
            },
        },
        "required": ["correct"],
    },
}
_VERDICT_TOOL_CHOICE = {"type": "function", "function": {"name": "submit_verdict"}}


# The grading model. A capable model judges semantic equivalence, non-answers,
# and added detail correctly, so no offline heuristics are needed. Uses the
# `grader` role (GLM 5.2, configured in models.yaml), falling back to `default`.
GRADER_ROLE = "grader"


async def grade(router, question: str, gold: str, predicted: str) -> bool:
    if not (predicted or "").strip():
        return False
    prompt = GRADER_TEMPLATE.format(
        question=question, gold_answer=gold, predicted=predicted
    )
    # Use the dedicated grader role only if a profile actually declares it
    # (select() always falls back to default, so check the profiles directly).
    has_grader = any(
        GRADER_ROLE in getattr(p, "roles", [])
        for p in getattr(router, "_profiles", {}).values()
    )
    role = GRADER_ROLE if has_grader else "default"
    # Generous budget: GLM 5.2 is a reasoning model and needs room to finish
    # its internal thinking before it emits the verdict tool call.
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await router.generate(
                role=role,
                messages=[{"role": "user", "content": prompt}],
                tools=[_VERDICT_TOOL],
                tool_choice=_VERDICT_TOOL_CHOICE,
                max_tokens=2048,
            )
            for call in response.tool_calls:
                if call.name == "submit_verdict":
                    return bool(call.arguments.get("correct"))
            # Forced tool_choice should guarantee a verdict call. A missing one
            # is a real failure — retry rather than guess from prose.
            last_error = RuntimeError("grader returned no submit_verdict call")
        except Exception as e:
            last_error = e
        if attempt < 2:
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"grading failed after retries: {last_error}")


# ── Runner ───────────────────────────────────────────────────────────────────

def _build_researcher(config_path: str | None, tier_name: str):
    from ..core.config import load_config
    from ..core.types import TIER_PRESETS, Tier
    from ..llm.router import ModelRouter
    from ..subagents.researcher import ResearcherSubAgent
    from ..tools.registry import ToolRegistry
    from ..tools.web_ops import FetchUrlTool, LookupDocsTool, WebSearchTool

    router_cfg, agent_cfg = load_config(config_path)
    router = ModelRouter(router_cfg)
    registry = ToolRegistry()
    registry.register_all([
        WebSearchTool(config=agent_cfg.web_search),
        FetchUrlTool(cache_dir=_DATA_DIR / "web_cache"),
        LookupDocsTool(config=agent_cfg.web_search),
    ])
    tier_cfg = TIER_PRESETS[Tier(tier_name)]
    sub = ResearcherSubAgent(router, registry, ".", tier_config=tier_cfg)
    return router, sub


# Per-task tool-call trajectory (None disables capture for the current task).
_TRACE_CALLS: "contextvars.ContextVar[list | None]" = contextvars.ContextVar(
    "browsecomp_trace_calls", default=None
)
# Guards against wrapping the registry's safe_execute more than once.
_TRACE_HOOK_INSTALLED: set[int] = set()


def _install_trace_hook(registry) -> None:
    """Wrap registry.safe_execute ONCE with a context-aware trajectory recorder."""
    if registry is None or id(registry) in _TRACE_HOOK_INSTALLED:
        return
    original = registry.safe_execute

    async def traced(name: str, arguments: dict, **kw) -> str:
        calls = _TRACE_CALLS.get()
        t0 = time.monotonic()
        res = await original(name, arguments, **kw)
        if calls is not None:
            calls.append({
                "n": len(calls) + 1,
                "tool": name,
                "args": {k: str(v)[:160] for k, v in arguments.items()},
                "result_preview": res[:200],
                "result_chars": len(res),
                "seconds": round(time.monotonic() - t0, 2),
            })
        return res

    registry.safe_execute = traced  # type: ignore[assignment]
    _TRACE_HOOK_INSTALLED.add(id(registry))


async def run_question(sub, router, row: dict[str, str], trace: bool = True) -> dict:
    started = time.monotonic()
    task = QUERY_TEMPLATE.format(question=row["question"])

    # Capture this question's tool-call trajectory. A ContextVar isolates the
    # list per asyncio task, so concurrent questions sharing one registry (and
    # the executor's own parallel tool batches, which inherit the context)
    # attribute calls to the right question without races.
    tool_calls: list[dict] = []
    token = _TRACE_CALLS.set(tool_calls if trace else None)
    _install_trace_hook(getattr(sub, "registry", None))
    throttle_before = getattr(router, "_throttle_wait_total", 0.0)
    try:
        result = await sub.run(task, context="")
        output = result.output or ""
    except Exception as e:
        output = f"ERROR: {e}"
    finally:
        _TRACE_CALLS.reset(token)

    predicted = extract_final_answer(output)
    try:
        correct = await grade(router, row["question"], row["answer"], predicted)
    except Exception:
        correct = False
    total = time.monotonic() - started
    tool_secs = sum(c.get("seconds", 0) for c in tool_calls)
    throttle_secs = getattr(router, "_throttle_wait_total", 0.0) - throttle_before
    return {
        "question": row["question"][:300],
        "gold_answer": row["answer"],
        "predicted": predicted,
        "correct": correct,
        "time_seconds": round(total, 1),
        # Where the wall-clock went: LLM (incl. our self-throttle) vs tools.
        "tool_seconds": round(tool_secs, 1),
        "throttle_seconds": round(throttle_secs, 1),
        "llm_seconds": round(total - tool_secs, 1),
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
    }


def _mp_worker(payload: tuple) -> dict:
    """Runs ONE question in a fresh process: own router, own event loop.

    Module-level and picklable so ProcessPoolExecutor (spawn) can call it.
    """
    row, config_path, tier = payload
    router, sub = _build_researcher(config_path, tier)
    return asyncio.run(run_question(sub, router, row))


def _run_multiprocess(rows: list[dict], args: argparse.Namespace) -> dict:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    out = Path(args.output)
    results: list[dict] = []
    payloads = [(row, args.config, args.tier) for row in rows]
    print(f"Process-parallel: {args.workers} workers, {len(rows)} questions", flush=True)

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_mp_worker, p) for p in payloads]
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception as e:
                r = {"question": "", "gold_answer": "", "predicted": f"WORKER ERROR: {e}",
                     "correct": False, "time_seconds": 0.0}
            results.append(r)
            mark = "PASS" if r.get("correct") else "FAIL"
            print(f"[{len(results)}/{len(rows)}] {mark}  ({r.get('time_seconds')}s, "
                  f"llm={r.get('llm_seconds','?')}s throttle={r.get('throttle_seconds','?')}s)  "
                  f"gold={r.get('gold_answer','')[:36]!r} pred={r.get('predicted','')[:36]!r}",
                  flush=True)

    passed = sum(1 for r in results if r.get("correct"))
    summary = {
        "benchmark": "BrowseComp", "total": len(results), "passed": passed,
        "success_rate": round(100.0 * passed / len(results), 2) if results else 0.0,
        "tier": args.tier, "seed": args.seed, "workers": args.workers,
        "finished_at": datetime.now(timezone.utc).isoformat(), "results": results,
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # Throttle/latency summary — verifies where the wall-clock actually went.
    avg_llm = sum(r.get("llm_seconds", 0) for r in results) / max(1, len(results))
    avg_thr = sum(r.get("throttle_seconds", 0) for r in results) / max(1, len(results))
    print(f"\nSuccess rate: {summary['success_rate']}%  ({passed}/{len(results)})")
    print(f"avg LLM time/question: {avg_llm:.0f}s | avg self-throttle/question: {avg_thr:.0f}s")
    print(f"Results written to {out}")
    return summary


async def run_benchmark(args: argparse.Namespace) -> dict:
    rows = await fetch_dataset()
    print(f"Dataset: {len(rows)} questions loaded and decrypted")

    if args.limit and args.limit < len(rows):
        rng = random.Random(args.seed)
        rows = rng.sample(rows, args.limit)
        print(f"Sampled {len(rows)} questions (seed={args.seed})")

    # Keep only specific positions of the sample (0-based) — lets you re-run
    # exactly the questions that failed before, preserving their identity.
    if getattr(args, "indices", None):
        picked = [rows[i] for i in args.indices if 0 <= i < len(rows)]
        rows = picked
        print(f"Running {len(rows)} selected indices: {args.indices}")

    if args.dry_run:
        for i, row in enumerate(rows[:3], start=1):
            print(f"\n[{i}] {row['question'][:200]}…")
        return {"total": len(rows), "dry_run": True}

    # Process-parallel path: each question runs in its own OS process with its
    # OWN ModelRouter, so there is no shared rate-limit throttle or shared
    # state across workers (the Go-goroutine equivalent for Python's GIL).
    if getattr(args, "workers", 0) and args.workers > 1:
        return _run_multiprocess(rows, args)

    router, sub = _build_researcher(args.config, args.tier)

    results: list[dict] = []
    sem = asyncio.Semaphore(max(1, args.concurrency))
    out = Path(args.output)

    def _write_partial() -> None:
        passed_so_far = sum(1 for r in results if r["correct"])
        out.write_text(json.dumps({
            "benchmark": "BrowseComp",
            "in_progress": True,
            "completed": len(results),
            "total": len(rows),
            "passed": passed_so_far,
            "success_rate": round(100.0 * passed_so_far / len(results), 2) if results else 0.0,
            "tier": args.tier,
            "seed": args.seed,
            "results": results,
        }, indent=2), encoding="utf-8")

    async def _one(i: int, row: dict) -> None:
        async with sem:
            r = await run_question(sub, router, row)
            results.append(r)
            mark = "PASS" if r["correct"] else "FAIL"
            print(f"[{len(results)}/{len(rows)}] {mark}  ({r['time_seconds']}s)  "
                  f"gold={r['gold_answer'][:40]!r} predicted={r['predicted'][:40]!r}",
                  flush=True)
            # Checkpoint so a crash/interrupt never loses completed work
            if len(results) % 10 == 0:
                _write_partial()

    await asyncio.gather(*[_one(i, row) for i, row in enumerate(rows)])

    passed = sum(1 for r in results if r["correct"])
    summary = {
        "benchmark": "BrowseComp",
        "total": len(results),
        "passed": passed,
        "success_rate": round(100.0 * passed / len(results), 2) if results else 0.0,
        "tier": args.tier,
        "seed": args.seed,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    out = Path(args.output)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSuccess rate: {summary['success_rate']}%  ({passed}/{len(results)})")
    print(f"Results written to {out}")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run BrowseComp against Zircon's web-research pipeline")
    p.add_argument("--limit", type=int, default=10,
                   help="Number of questions to sample (0 = all 1266; default 10)")
    p.add_argument("--seed", type=int, default=42, help="Sampling seed (default 42)")
    p.add_argument("--indices", type=int, nargs="+", default=None,
                   help="Run only these 0-based positions of the sample")
    p.add_argument("--tier", default="balanced", choices=["low", "balanced", "quality"])
    p.add_argument("--config", default=None, help="Path to models.yaml (default: project config)")
    p.add_argument("--workers", type=int, default=0,
                   help="Process-parallel workers (each its own process+router). 0 = async single-process")
    p.add_argument("--concurrency", type=int, default=1,
                   help="Concurrent questions (default 1; mind rate limits)")
    p.add_argument("--output", default=str(_DEFAULT_OUTPUT), help="Results JSON path")
    p.add_argument("--dry-run", action="store_true",
                   help="Download/decrypt the dataset and preview questions; no LLM calls")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    # Relative imports require package context: run via
    #   python -m zirconAgent.benchmark.browsecomp
    main()
