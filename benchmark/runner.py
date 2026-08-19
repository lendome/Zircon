from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .results import ExerciseResult


def discover_exercises(
    exercises_root: str | Path,
    languages: list[str] | None = None,
    keywords: list[str] | None = None,
    num_tests: int = 0,
) -> list[tuple[str, str, Path]]:
    root = Path(exercises_root)
    exercises: list[tuple[str, str, Path]] = []

    if languages is None:
        languages = ["python", "javascript"]

    for lang in languages:
        practice_dir = root / lang / "exercises" / "practice"
        if not practice_dir.is_dir():
            print(f"  WARNING: no practice dir for {lang}: {practice_dir}")
            continue

        for exercise_dir in sorted(practice_dir.iterdir()):
            if not exercise_dir.is_dir():
                continue
            meta = exercise_dir / ".meta" / "config.json"
            if not meta.is_file():
                continue

            name = exercise_dir.name

            if keywords:
                if not any(kw.lower() in name.lower() for kw in keywords):
                    continue

            exercises.append((name, lang, exercise_dir))

    if num_tests and num_tests < len(exercises):
        exercises = exercises[:num_tests]

    return exercises


def load_exercise(exercise_dir: Path, work_dir: Path) -> dict[str, Any] | None:
    meta_path = exercise_dir / ".meta" / "config.json"
    if not meta_path.is_file():
        return None

    with open(meta_path) as f:
        meta = json.load(f)

    files_info = meta.get("files", {})
    solution_files = files_info.get("solution", [])
    test_files = files_info.get("test", [])
    example_files = files_info.get("example", [])

    if not solution_files or not test_files:
        return None

    for item in exercise_dir.iterdir():
        if item.name == ".meta":
            continue
        if item.is_file():
            shutil.copy2(item, work_dir / item.name)
        elif item.is_dir():
            shutil.copytree(item, work_dir / item.name, dirs_exist_ok=True)

    docs_dir = exercise_dir / ".docs"
    parts: list[str] = []

    instructions_file = docs_dir / "instructions.md"
    if instructions_file.is_file():
        parts.append(instructions_file.read_text(encoding="utf-8"))

    append_file = docs_dir / "instructions.append.md"
    if append_file.is_file():
        parts.append(append_file.read_text(encoding="utf-8"))

    blurb = meta.get("blurb", "")
    if blurb:
        parts.insert(0, f"# {blurb}\n")

    instructions = "\n\n".join(parts)
    if not instructions.strip():
        return None

    lang = "unknown"
    for segment in exercise_dir.parts:
        if segment in ("python", "javascript", "rust", "go", "cpp", "java"):
            lang = segment
            break

    return {
        "name": exercise_dir.name,
        "language": lang,
        "solution_files": solution_files,
        "test_files": test_files,
        "instructions": instructions,
        "blurb": blurb,
    }


def build_task_prompt(info: dict[str, Any]) -> str:
    solution_files = info["solution_files"]
    test_files = info["test_files"]

    prompt = info["instructions"]

    prompt += "\n\n---\n\n"
    prompt += "## Your Task\n\n"
    prompt += f"Edit the following file(s) to implement the solution: {', '.join(solution_files)}\n\n"
    prompt += "The tests are in: " + ", ".join(test_files) + "\n"
    prompt += "Do NOT modify the test files.\n"
    prompt += "Implement the solution so all tests pass.\n"

    return prompt


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    import json
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


async def run_agent(
    work_dir: Path,
    task_prompt: str,
    tier: str = "quality",
) -> dict[str, Any]:
    import sys

    agent_root = Path(__file__).parent.parent
    parent_dir = str(agent_root.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    from zirconAgent.core.agent import Agent
    from zirconAgent.core.types import Tier, TaskStatus

    tier_enum = Tier(tier)

    agent = Agent(repo_path=str(work_dir), tier=tier_enum)

    agent.tier_cfg.gatekeeper_mode = "rule_only"
    agent.tier_cfg.skip_planner = False  # keep planner but reduce consensus samples
    agent.tier_cfg.multi_sample_consensus = False
    agent.tier_cfg.multi_sample_n = 1

    for profile in agent.router._profiles.values():
        if profile.max_tokens > 4096:
            profile.max_tokens = 4096
    if agent.tier_cfg.default_max_tokens > 4096:
        agent.tier_cfg.default_max_tokens = 4096

    if agent.executor.tier.default_max_tokens > 4096:
        agent.executor.tier.default_max_tokens = 4096

    import json as _json
    _orig_build = agent.router._build_payload

    def _safe_build(profile, messages, tools, max_tokens, stream=False, **kwargs):
        sanitized = _sanitize_messages(messages)
        return _orig_build(profile, sanitized, tools, max_tokens, stream, **kwargs)

    agent.router._build_payload = _safe_build

    conversation_log: list[dict] = []
    _orig_call = agent.router._call

    async def _logging_call(profile, messages, tools, max_tokens, **kwargs):
        payload = agent.router._build_payload(profile, messages, tools, max_tokens, stream=False)
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
            resp = await _orig_call(profile, messages, tools, max_tokens, **kwargs)
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
        "trace": [{"phase": t.phase, "detail": t.detail, "payload": t.payload} for t in result.trace],
        "context_history": agent.context.history,
        "conversation_log": conversation_log,
    }


def run_tests(work_dir: Path, language: str, test_files: list[str]) -> tuple[bool, str]:
    if language == "python":
        return _run_python_tests(work_dir, test_files)
    elif language == "javascript":
        return _run_js_tests(work_dir, test_files)
    else:
        return False, f"Unsupported language: {language}"


def _run_python_tests(work_dir: Path, test_files: list[str]) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    cmd = ["pytest", "-x", "--tb=short", "-q"] + test_files
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        output = proc.stdout + proc.stderr
        return proc.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out (60s)"
    except FileNotFoundError:
        return False, "pytest not found. Install with: pip install pytest"


def _run_js_tests(work_dir: Path, test_files: list[str]) -> tuple[bool, str]:
    if (work_dir / "package.json").is_file():
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return False, "npm install timed out (120s)"
        except FileNotFoundError:
            return False, "npm not found. Install Node.js."

    cmd = ["npm", "test"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = proc.stdout + proc.stderr
        return proc.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Test execution timed out (60s)"
    except FileNotFoundError:
        return False, "npm not found. Install Node.js."


_LOG_DIR = Path(__file__).parent / "failure_logs"


def _dump_failure_log(
    exercise_name: str,
    language: str,
    work_dir: Path,
    task_prompt: str,
    agent_result: dict[str, Any],
    test_output: str,
) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    solution_files: dict[str, str] = {}
    for f in work_dir.iterdir():
        if f.is_file() and f.suffix in (".py", ".js", ".ts"):
            try:
                solution_files[f.name] = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    log = {
        "exercise": exercise_name,
        "language": language,
        "task_prompt": task_prompt,
        "agent_answer": agent_result.get("answer", ""),
        "agent_status": agent_result.get("status", ""),
        "files_modified": agent_result.get("files_modified", []),
        "tokens_used": agent_result.get("tokens_used", 0),
        "test_output": test_output,
        "solution_files": solution_files,
        "trace": agent_result.get("trace", []),
        "context_history": agent_result.get("context_history", []),
        "conversation_log": agent_result.get("conversation_log", []),
    }

    safe_name = exercise_name.replace("/", "_").replace("\\", "_")
    log_path = _LOG_DIR / f"{language}_{safe_name}.json"
    log_path.write_text(json.dumps(log, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"    -> failure log: {log_path}", flush=True)


async def run_single_exercise(
    exercise_name: str,
    language: str,
    exercise_dir: Path,
    tier: str = "quality",
) -> ExerciseResult:
    start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix=f"bench_{exercise_name}_"))
    agent_result: dict[str, Any] = {}

    try:
        info = load_exercise(exercise_dir, tmp)
        if info is None:
            return ExerciseResult(
                exercise=exercise_name,
                language=language,
                passed=False,
                time_seconds=time.time() - start,
                error="Failed to load exercise",
            )

        task_prompt = build_task_prompt(info)

        try:
            agent_result = await asyncio.wait_for(
                run_agent(tmp, task_prompt, tier=tier),
                timeout=600,  # 10 minutes max per exercise
            )
        except asyncio.TimeoutError:
            _dump_failure_log(
                exercise_name, language, tmp, task_prompt,
                agent_result, "Agent timed out (600s)",
            )
            return ExerciseResult(
                exercise=exercise_name,
                language=language,
                passed=False,
                time_seconds=time.time() - start,
                error="Agent timed out (600s)",
            )
        except Exception as e:
            _dump_failure_log(
                exercise_name, language, tmp, task_prompt,
                agent_result, f"Agent error: {e}",
            )
            return ExerciseResult(
                exercise=exercise_name,
                language=language,
                passed=False,
                time_seconds=time.time() - start,
                error=f"Agent error: {e}",
            )

        passed, test_output = run_tests(tmp, language, info["test_files"])

        if not passed:
            _dump_failure_log(
                exercise_name, language, tmp,
                task_prompt, agent_result, test_output,
            )

        return ExerciseResult(
            exercise=exercise_name,
            language=language,
            passed=passed,
            time_seconds=time.time() - start,
            test_output=test_output[:2000],
            agent_answer=agent_result.get("answer", "")[:500],
        )
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
