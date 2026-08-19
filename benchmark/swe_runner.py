from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any



@dataclass
class SWETaskResult:
    instance_id: str
    repo: str
    base_commit: str
    passed: bool = False
    resolved: bool = False
    time_seconds: float = 0.0
    test_output: str = ""
    agent_answer: str = ""
    error: str = ""
    patch_generated: str = ""


@dataclass
class SWEBenchmarkStats:
    total: int = 0
    resolved: int = 0
    failed_tests: int = 0
    errors: int = 0
    resolution_rate: float = 0.0
    avg_time: float = 0.0
    total_time: float = 0.0


@dataclass
class SWEBenchmarkRun:
    results: list[SWETaskResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    dataset_name: str = ""
    split: str = "test"
    tier: str = "quality"

    def add(self, result: SWETaskResult) -> None:
        self.results.append(result)

    def stats(self) -> SWEBenchmarkStats:
        total = len(self.results)
        if total == 0:
            return SWEBenchmarkStats()
        resolved = sum(1 for r in self.results if r.resolved)
        errors = sum(1 for r in self.results if r.error)
        failed_tests = total - resolved - errors
        total_time = sum(r.time_seconds for r in self.results)
        return SWEBenchmarkStats(
            total=total,
            resolved=resolved,
            failed_tests=failed_tests,
            errors=errors,
            resolution_rate=resolved / total if total else 0.0,
            avg_time=total_time / total if total else 0.0,
            total_time=total_time,
        )

    def print_summary(self) -> None:
        s = self.stats()
        print("\n" + "=" * 60)
        print("  SWE-BENCH PRO RESULTS")
        print("=" * 60)
        print(f"  Dataset:   {self.dataset_name}")
        print(f"  Split:     {self.split}")
        print(f"  Tier:      {self.tier}")
        print(f"  Started:   {self.started_at}")
        print(f"  Finished:  {self.finished_at}")
        print(f"  Total:     {s.total} tasks")
        print(f"  Resolved:  {s.resolved}")
        print(f"  Failed:    {s.failed_tests}")
        print(f"  Errors:    {s.errors}")
        print(f"  Rate:      {s.resolution_rate:.1%}")
        print(f"  Avg Time:  {s.avg_time:.1f}s")
        print(f"  Total:     {s.total_time:.1f}s")

        print("\n" + "-" * 60)
        print("  PER-TASK RESULTS")
        print("-" * 60)
        for r in self.results:
            status = "RESOLVED" if r.resolved else ("ERR!" if r.error else "FAIL")
            print(f"  [{status}] {r.instance_id} ({r.time_seconds:.1f}s)")
            if r.error:
                print(f"         {r.error[:120]}")
        print("=" * 60)

    def save_json(self, path: str | Path) -> None:
        data = {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "tier": self.tier,
            "stats": asdict(self.stats()),
            "results": [asdict(r) for r in self.results],
        }
        Path(path).write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False))

    def save_predictions(self, path: str | Path) -> None:
        with open(path, "w") as f:
            for result in self.results:
                if result.error or not result.patch_generated:
                    continue
                pred = {
                    "instance_id": result.instance_id,
                    "model_name_or_path": f"agent-{self.tier}",
                    "model_patch": result.patch_generated,
                }
                f.write(json.dumps(pred) + "\n")



def load_swe_tasks(
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    split: str = "test",
    instance_ids: list[str] | None = None,
    num_tasks: int = 0,
) -> list[dict]:
    try:
        from datasets import load_dataset
    except ImportError:
        raise ImportError("Install the datasets library: pip install datasets")

    dataset = load_dataset(dataset_name, split=split)
    tasks = []
    for item in dataset:
        if instance_ids and item["instance_id"] not in instance_ids:
            continue
        tasks.append({
            "instance_id": item["instance_id"],
            "repo": item["repo"],
            "base_commit": item["base_commit"],
            "problem_statement": item["problem_statement"],
            "hints_text": item.get("hints_text", ""),
            "test_patch": item.get("test_patch", ""),
            "created_at": item.get("created_at", ""),
            "version": item.get("version", ""),
        })

    if num_tasks and num_tasks < len(tasks):
        tasks = tasks[:num_tasks]

    return tasks



def build_swe_prompt(task: dict) -> str:
    prompt = f"# GitHub Issue: {task['repo']}\n\n"
    prompt += task["problem_statement"]

    if task.get("hints_text"):
        prompt += f"\n\n## Additional Context\n\n{task['hints_text']}"

    prompt += "\n\n## Your Task\n\n"
    prompt += (
        "Analyze the codebase and implement a fix for this issue. "
        "Make sure to run the relevant tests to verify your solution works correctly. "
        "Do NOT modify test files — only fix the source code."
    )
    return prompt



def _sanitize_messages(messages: list[dict]) -> list[dict]:
    result = []
    for msg in messages:
        if "tool_calls" in msg:
            new_tcs = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, dict):
                    fn = {**fn, "arguments": json.dumps(args)}
                    tc = {**tc, "function": fn}
                new_tcs.append(tc)
            msg = {**msg, "tool_calls": new_tcs}

        if "tool_call" in msg and "tool_calls" not in msg:
            tc = msg["tool_call"]
            args = tc.get("arguments", {})
            if isinstance(args, dict):
                args = json.dumps(args)
            msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tc.get("id", "unknown"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": args,
                    },
                }],
            }

        if msg.get("role") == "tool" and "tool_call_id" not in msg:
            msg = {**msg, "tool_call_id": msg.get("tool_name", "unknown")}

        result.append(msg)
    return result



async def run_agent_for_swe(
    work_dir: Path,
    task_prompt: str,
    tier: str = "quality",
) -> dict[str, Any]:
    agent_root = Path(__file__).parent.parent
    parent_dir = str(agent_root.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from zirconAgent.core.agent import Agent
    from zirconAgent.core.types import Tier, TaskStatus

    tier_enum = Tier(tier)
    agent = Agent(repo_path=str(work_dir), tier=tier_enum)

    agent.tier_cfg.gatekeeper_mode = "rule_only"
    agent.tier_cfg.multi_sample_consensus = False
    agent.tier_cfg.multi_sample_n = 1

    for profile in agent.router._profiles.values():
        if profile.max_tokens > 4096:
            profile.max_tokens = 4096
    if agent.tier_cfg.default_max_tokens > 4096:
        agent.tier_cfg.default_max_tokens = 4096
    if agent.executor.tier.default_max_tokens > 4096:
        agent.executor.tier.default_max_tokens = 4096

    _orig_build = agent.router._build_payload

    def _safe_build(profile, messages, tools, max_tokens, stream=False):
        sanitized = _sanitize_messages(messages)
        return _orig_build(profile, sanitized, tools, max_tokens, stream)

    agent.router._build_payload = _safe_build

    conversation_log: list[dict] = []
    _orig_call = agent.router._call

    async def _logging_call(profile, messages, tools, max_tokens):
        entry: dict[str, Any] = {
            "profile": profile.name,
            "model": profile.model,
            "max_tokens": max_tokens,
            "messages_in": [
                {
                    "role": m.get("role", "?"),
                    "content": (m.get("content") or "")[:2000],
                    "tool_calls": m.get("tool_calls"),
                }
                for m in messages
            ],
        }
        try:
            resp = await _orig_call(profile, messages, tools, max_tokens)
            entry["response_content"] = (resp.content or "")[:2000]
            entry["response_tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in resp.tool_calls
            ]
            entry["usage"] = resp.usage
            return resp
        except Exception as e:
            entry["error"] = str(e)
            raise
        finally:
            conversation_log.append(entry)

    agent.router._call = _logging_call

    result = await agent.solve(task_prompt)

    max_approvals = 3
    approvals = 0
    while result.status == TaskStatus.AWAITING_INPUT and approvals < max_approvals:
        agent.submit_feedback("Proceed with the plan.")
        result = await agent.solve("Proceed with the plan as proposed.")
        approvals += 1

    return {
        "success": result.success,
        "answer": result.answer,
        "files_modified": result.files_modified,
        "tokens_used": result.tokens_used,
        "status": result.status.value,
        "trace": [
            {"phase": t.phase, "detail": t.detail, "payload": t.payload}
            for t in result.trace
        ],
        "conversation_log": conversation_log,
    }



def _extract_patch(work_dir: Path, files_modified: list[str]) -> str:
    try:
        cmd = ["git", "diff", "HEAD"]
        if files_modified:
            cmd.extend(files_modified)
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.stdout
    except Exception:
        return ""



_LOG_DIR = Path(__file__).parent / "failure_logs"


def _dump_swe_failure_log(
    instance_id: str,
    work_dir: Path,
    task_prompt: str,
    agent_result: dict[str, Any],
    test_output: str,
) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    log = {
        "instance_id": instance_id,
        "task_prompt": task_prompt,
        "agent_answer": agent_result.get("answer", ""),
        "agent_status": agent_result.get("status", ""),
        "files_modified": agent_result.get("files_modified", []),
        "tokens_used": agent_result.get("tokens_used", 0),
        "test_output": test_output,
        "conversation_log": agent_result.get("conversation_log", []),
    }

    safe_name = instance_id.replace("/", "_").replace("\\", "_")
    log_path = _LOG_DIR / f"swe_{safe_name}.json"
    log_path.write_text(
        json.dumps(log, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"    -> failure log: {log_path}", flush=True)



async def run_single_swe_task(
    task: dict,
    tier: str = "quality",
    timeout: int = 600,
    work_dir: Path | None = None,
) -> SWETaskResult:
    start = time.time()
    cleanup = work_dir is None
    work_dir = work_dir or Path(tempfile.mkdtemp(prefix=f"swe_{task['instance_id']}_"))
    agent_result: dict[str, Any] = {}

    try:
        repo_url = f"https://github.com/{task['repo']}.git"
        clone_proc = subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(work_dir)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone_proc.returncode != 0:
            return SWETaskResult(
                instance_id=task["instance_id"],
                repo=task["repo"],
                base_commit=task["base_commit"],
                time_seconds=time.time() - start,
                error=f"git clone failed: {clone_proc.stderr[:200]}",
            )

        checkout_proc = subprocess.run(
            ["git", "checkout", "--quiet", task["base_commit"]],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if checkout_proc.returncode != 0:
            return SWETaskResult(
                instance_id=task["instance_id"],
                repo=task["repo"],
                base_commit=task["base_commit"],
                time_seconds=time.time() - start,
                error=f"git checkout failed: {checkout_proc.stderr[:200]}",
            )

        task_prompt = build_swe_prompt(task)

        try:
            agent_result = await asyncio.wait_for(
                run_agent_for_swe(work_dir, task_prompt, tier=tier),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _dump_swe_failure_log(
                task["instance_id"], work_dir, task_prompt,
                agent_result, f"Agent timed out ({timeout}s)",
            )
            return SWETaskResult(
                instance_id=task["instance_id"],
                repo=task["repo"],
                base_commit=task["base_commit"],
                time_seconds=time.time() - start,
                error=f"Agent timed out ({timeout}s)",
            )
        except Exception as e:
            _dump_swe_failure_log(
                task["instance_id"], work_dir, task_prompt,
                agent_result, f"Agent error: {e}",
            )
            return SWETaskResult(
                instance_id=task["instance_id"],
                repo=task["repo"],
                base_commit=task["base_commit"],
                time_seconds=time.time() - start,
                error=f"Agent error: {e}",
            )

        files_modified = agent_result.get("files_modified", [])
        patch = _extract_patch(work_dir, files_modified)

        if not patch:
            _dump_swe_failure_log(
                task["instance_id"], work_dir, task_prompt,
                agent_result, "No patch generated",
            )

        return SWETaskResult(
            instance_id=task["instance_id"],
            repo=task["repo"],
            base_commit=task["base_commit"],
            time_seconds=time.time() - start,
            agent_answer=agent_result.get("answer", "")[:500],
            patch_generated=patch,
        )

    except Exception as e:
        return SWETaskResult(
            instance_id=task["instance_id"],
            repo=task["repo"],
            base_commit=task["base_commit"],
            time_seconds=time.time() - start,
            error=f"Setup error: {e}",
        )
    finally:
        if cleanup:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass



def evaluate_with_harness(
    run: SWEBenchmarkRun,
    output_dir: str | Path = "swe_evaluation_results",
    max_workers: int = 4,
    cache_level: str = "env",
    clean: bool = False,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions_path = output_dir / "predictions.jsonl"
    run.save_predictions(predictions_path)

    run_id = f"agent_{run.tier}_{int(time.time())}"
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", run.dataset_name,
        "--predictions_path", str(predictions_path),
        "--max_workers", str(max_workers),
        "--cache_level", cache_level,
        "--run_id", run_id,
    ]
    if clean:
        cmd.append("--clean")

    print(f"Running SWE-bench harness: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        print(f"Harness stderr: {proc.stderr}")
        raise RuntimeError(f"SWE-bench harness failed (exit {proc.returncode})")

    results_file = output_dir / "evaluation_results" / "results.json"
    if results_file.exists():
        with open(results_file) as f:
            eval_results = json.load(f)

        resolved_ids = set()
        if isinstance(eval_results, dict):
            for iid, status in eval_results.get("resolved", {}).items():
                if status:
                    resolved_ids.add(iid)
        elif isinstance(eval_results, list):
            for entry in eval_results:
                if entry.get("resolved"):
                    resolved_ids.add(entry["instance_id"])

        for result in run.results:
            if result.instance_id in resolved_ids:
                result.resolved = True
                result.passed = True

        return eval_results

    raise RuntimeError("Evaluation results file not found after harness run")
