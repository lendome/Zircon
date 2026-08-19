"""Developer-workflow tools: run_task, verify_determinism, run_profiler.

These are higher-level, outcome-oriented wrappers around shell execution so
the agent never has to hand-roll OS-specific redirects, determinism harnesses,
or manual timer instrumentation again:

- ``run_task`` — run a command with structured stdout/stderr capture and an
  optional ``save_output_to`` file (golden output) written by Python with LF
  newlines (never a shell redirect).
- ``verify_determinism`` — run a command N times and report whether the
  normalized output is byte-identical, with first-diff context when not.
- ``run_profiler`` — wrap the command in its ecosystem's native profiler
  (cProfile / node --cpu-prof / go test -cpuprofile) and return a clean top-N
  bottleneck list. Replaces hand-placed timers entirely.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .base import Tool
from ..core.profiling import (
    build_profile_command,
    detect_profiler,
    format_hotspots,
    parse_cprofile,
    parse_cpuprofile,
    parse_pprof_top,
    unsupported_guidance,
)
from ..core.shell_env import run_capture, resolve_shell, shell_syntax_hint


def _first_diff_line(a: str, b: str) -> tuple[int, str, str] | None:
    """Return (line_no, line_a, line_b) of the first differing line, else None."""
    lines_a = a.splitlines()
    lines_b = b.splitlines()
    for i, (la, lb) in enumerate(zip(lines_a, lines_b), 1):
        if la != lb:
            return i, la, lb
    if len(lines_a) != len(lines_b):
        idx = min(len(lines_a), len(lines_b)) + 1
        la = lines_a[idx - 1] if idx <= len(lines_a) else "<missing>"
        lb = lines_b[idx - 1] if idx <= len(lines_b) else "<missing>"
        return idx, la, lb
    return None


class RunTaskTool(Tool):
    def __init__(self, repo_path: str, pinning_enabled: bool = True):
        self.repo_path = Path(repo_path).resolve()
        self._pinning = pinning_enabled

    @property
    def name(self) -> str:
        return "run_task"

    @property
    def description(self) -> str:
        hint = shell_syntax_hint(resolve_shell(self._pinning))
        return (
            "Run a command and capture its output in a clean, structured way. "
            "Unlike run_command, stdout and stderr are returned in separate "
            "labeled sections, and you can save the combined output to a file "
            "(for golden-output comparisons) with save_output_to — the file is "
            "written by the tool with LF newlines, never via shell redirects. "
            "Use this instead of run_command whenever you need to compare, "
            "diff, or post-process command output. " + hint
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run"},
                "cwd": {"type": "string", "description": "Working directory (optional, default: repo root)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"},
                "capture_stdout": {"type": "boolean", "description": "Include stdout in the result (default: true)"},
                "capture_stderr": {"type": "boolean", "description": "Include stderr in the result (default: true)"},
                "save_output_to": {
                    "type": "string",
                    "description": "Repo-relative file path to save combined output to (e.g. 'golden.txt'). Written with LF newlines.",
                },
                "max_output_chars": {"type": "integer", "description": "Max characters of output returned (default: 12000)"},
            },
            "required": ["command"],
        }

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 60,
        capture_stdout: bool = True,
        capture_stderr: bool = True,
        save_output_to: str | None = None,
        max_output_chars: int = 12000,
    ) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        save_path: Path | None = None
        if save_output_to:
            save_path = (self.repo_path / save_output_to).resolve()
            if not save_path.is_relative_to(self.repo_path):
                return f"Error: save_output_to must stay inside the repo: {save_output_to}"

        spec = resolve_shell(self._pinning)
        result = await run_capture(
            command,
            cwd=str(work_dir),
            timeout=float(timeout),
            spec=spec,
        )

        out = result.stdout if capture_stdout else ""
        err = result.stderr if capture_stderr else ""

        parts: list[str] = []
        if result.timed_out:
            parts.append(f"Command timed out after {timeout}s and was killed.")
        if out.strip():
            parts.append(f"STDOUT:\n{out.strip()}")
        if err.strip():
            parts.append(f"STDERR:\n{err.strip()}")
        if not result.timed_out:
            parts.append(f"Exit code: {result.exit_code}")

        if save_path is not None:
            combined = result.stdout
            if result.stderr:
                combined += ("\n" if combined and not combined.endswith("\n") else "") + result.stderr
            try:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                data = combined if combined.endswith("\n") or not combined else combined + "\n"
                save_path.write_text(data, encoding="utf-8", newline="\n")
                parts.append(f"saved_to: {save_path.relative_to(self.repo_path)} ({len(data)} bytes)")
            except Exception as e:
                parts.append(f"Error saving output to {save_output_to}: {e}")

        text = "\n\n".join(parts) if parts else "(no output)"
        if len(text) > max_output_chars:
            text = text[:max_output_chars] + "\n... (truncated)"
        return text

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class VerifyDeterminismTool(Tool):
    def __init__(self, repo_path: str, pinning_enabled: bool = True):
        self.repo_path = Path(repo_path).resolve()
        self._pinning = pinning_enabled

    @property
    def name(self) -> str:
        return "verify_determinism"

    @property
    def description(self) -> str:
        hint = shell_syntax_hint(resolve_shell(self._pinning))
        return (
            "Check whether a command produces stable output across repeated "
            "runs. Runs the command N times, normalizes line endings, and "
            "compares exit codes and stdout byte-for-byte. Reports "
            "DETERMINISTIC or NON-DETERMINISTIC with the first differing line "
            "as context. Use this instead of manually re-running a command "
            "and diffing outputs to hunt flaky tests, unstable ordering, or "
            "timestamp contamination. " + hint
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run repeatedly"},
                "runs": {"type": "integer", "description": "Number of runs (default: 3, max: 5)"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "timeout_per_run": {"type": "integer", "description": "Timeout per run in seconds (default: 30)"},
                "normalize": {"type": "boolean", "description": "Normalize CRLF and trailing whitespace before comparing (default: true)"},
            },
            "required": ["command"],
        }

    async def run(
        self,
        command: str,
        runs: int = 3,
        cwd: str | None = None,
        timeout_per_run: int = 30,
        normalize: bool = True,
    ) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        runs = max(2, min(5, int(runs)))
        spec = resolve_shell(self._pinning)
        wall_cap = runs * timeout_per_run + 10.0
        deadline = time.monotonic() + wall_cap

        outputs: list[str] = []
        exit_codes: list[int] = []
        durations: list[float] = []
        timed_out = False

        for i in range(runs):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            result = await run_capture(
                command,
                cwd=str(work_dir),
                timeout=min(float(timeout_per_run), remaining),
                spec=spec,
            )
            text = result.stdout
            if normalize:
                text = "\n".join(line.rstrip() for line in text.splitlines())
                text = text.rstrip("\n")
            outputs.append(text)
            exit_codes.append(result.exit_code)
            durations.append(result.duration)
            if result.timed_out:
                timed_out = True

        if len(outputs) < 2:
            return (
                f"INCONCLUSIVE: only {len(outputs)} run(s) completed within the "
                f"wall-clock cap ({wall_cap:.0f}s). Partial exit codes: {exit_codes}"
            )

        stable_exit = len(set(exit_codes)) == 1
        base = outputs[0]
        identical = all(o == base for o in outputs[1:])

        timing = ", ".join(f"{d:.2f}s" for d in durations)
        header_bits = [
            f"runs: {len(outputs)}",
            f"exit codes: {exit_codes}",
            f"run times: [{timing}]",
        ]

        if stable_exit and identical:
            return (
                f"DETERMINISTIC: {len(outputs)}/{len(outputs)} runs identical "
                f"(exit {exit_codes[0]}, stdout {len(base)} bytes).\n"
                + "\n".join(header_bits)
                + ("\nNote: at least one run hit its timeout." if timed_out else "")
            )

        lines: list[str] = []
        if not stable_exit:
            lines.append("NON-DETERMINISTIC: exit codes differ between runs.")
        else:
            lines.append("NON-DETERMINISTIC: stdout differs between runs.")
        lines.extend(header_bits)
        for i in range(1, len(outputs)):
            diff = _first_diff_line(base, outputs[i])
            if diff is None:
                continue
            line_no, la, lb = diff
            lines.append(
                f"\nFirst difference (run 1 vs run {i + 1}) at line {line_no}:"
            )
            lines.append(f"  run 1: {la[:200]}")
            lines.append(f"  run {i + 1}: {lb[:200]}")
            break
        if not stable_exit:
            lines.append("\nExit-code instability usually means flaky error paths or races.")
        else:
            lines.append(
                "\nCommon causes: timestamps, random ordering (dict/set iteration), "
                "PIDs, temp paths, or concurrency. Normalize or seed the source."
            )
        return "\n".join(lines)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p


class RunProfilerTool(Tool):
    def __init__(self, repo_path: str, pinning_enabled: bool = True):
        self.repo_path = Path(repo_path).resolve()
        self._pinning = pinning_enabled

    @property
    def name(self) -> str:
        return "run_profiler"

    @property
    def description(self) -> str:
        hint = shell_syntax_hint(resolve_shell(self._pinning))
        return (
            "Profile a command's CPU usage with its native profiler and return "
            "the top bottlenecks — use this instead of inserting manual timers "
            "or printf statements. Auto-detects: 'python script.py' (cProfile), "
            "'node script.js' (--cpu-prof), 'go test ...' (-cpuprofile + "
            "-memprofile + -mutexprofile). For 'go run' or prebuilt binaries, "
            "returns the benchmark-harness recipe instead (they cannot be "
            "profiled from outside). " + hint
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to profile"},
                "cwd": {"type": "string", "description": "Working directory (optional)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)"},
                "profiler": {
                    "type": "string",
                    "description": "Profiler: 'auto' (default), 'cprofile', 'node', or 'go'",
                },
                "top_n": {"type": "integer", "description": "Number of hotspots to report (default: 5)"},
            },
            "required": ["command"],
        }

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 120,
        profiler: str = "auto",
        top_n: int = 5,
    ) -> str:
        work_dir = self._resolve(cwd) if cwd else self.repo_path
        if not work_dir.is_dir():
            return f"Error: directory not found: {cwd or '.'}"

        top_n = max(1, min(20, int(top_n)))
        chosen = profiler if profiler != "auto" else (detect_profiler(command) or "")
        if chosen not in ("cprofile", "node", "go"):
            return unsupported_guidance(command)

        profile_dir = self.repo_path / ".zircon-code" / "profiles" / f"run_{int(time.time())}"
        plan = build_profile_command(command, chosen, profile_dir)
        if plan is None:
            return unsupported_guidance(command)

        spec = resolve_shell(self._pinning)
        result = await run_capture(
            plan.command,
            cwd=str(work_dir),
            timeout=float(timeout),
            spec=spec,
        )

        header: list[str] = []
        if result.timed_out:
            header.append(f"Warning: profiled command timed out after {timeout}s (partial profile).")
        elif result.exit_code != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()
            excerpt = "\n".join(tail[-5:])[:500]
            header.append(
                f"Warning: profiled command exited with code {result.exit_code}. "
                f"Output tail:\n{excerpt}"
            )

        hotspots = []
        extra: list[str] = []
        if chosen == "cprofile":
            hotspots = parse_cprofile(plan.profile_path, top_n)
            title = f"Top {top_n} by self time (cProfile):"
        elif chosen == "node":
            hotspots = parse_cpuprofile(plan.profile_path, top_n)
            title = f"Top {top_n} by self time (node --cpu-prof):"
        else:  # go
            hotspots = await self._pprof_top(plan.profile_path, work_dir, spec, top_n)
            title = f"Top {top_n} by self time (go pprof, cpu):"
            mutex_path = plan.extra_profiles.get("mutex")
            if mutex_path and Path(mutex_path).exists():
                mutex_top = await self._pprof_top(mutex_path, work_dir, spec, top_n, index="delay")
                if mutex_top:
                    extra.append(
                        "Top mutex contention (pprof):\n" + "\n".join(
                            f"  {h.self_time:.3f}s  {h.function}" for h in mutex_top
                        )
                    )

        summary = format_hotspots(title, hotspots, extra_sections=extra, command=command)
        text = "\n".join([*header, summary]) if header else summary

        # Best-effort cleanup of profile artifacts; failures are harmless.
        try:
            import shutil as _shutil
            _shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
        return text

    async def _pprof_top(self, profile_path: str, work_dir: Path, spec, top_n: int, index: str = "") -> list:
        path = Path(profile_path)
        if not path.exists() or path.stat().st_size == 0:
            return []
        args = ["go", "tool", "pprof", "-top", f"-nodecount={top_n}"]
        if index:
            args.append(f"-sample_index={index}")
        args.append(str(path))
        cmd = " ".join(args)
        result = await run_capture(cmd, cwd=str(work_dir), timeout=30.0, spec=spec)
        if result.exit_code != 0:
            return []
        return parse_pprof_top(result.stdout, top_n)

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.repo_path / p
