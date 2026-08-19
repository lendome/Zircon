"""Profiler wrapping and output parsing for the run_profiler tool.

The agent previously hand-inserted ``time.Now()`` / ``fmt.Printf`` timers to
find bottlenecks. This module wraps a command in its ecosystem's native
profiler and parses the output into a clean top-N bottleneck list:

- ``python x.py``     -> ``python -m cProfile -o <tmp>`` + stdlib pstats
- ``node x.js``       -> ``node --cpu-prof`` + .cpuprofile JSON parsing
- ``go test ...``     -> ``-cpuprofile/-memprofile/-mutexprofile`` + pprof -top

Anything else (``go run``, prebuilt binaries, arbitrary shell) is NOT
generically instrumentable; ``build_profile_command`` returns None for those
and the tool layer responds with an actionable guidance message instead of
silently doing nothing.

All functions are pure/sync (subprocess via asyncio lives in tools/dev_ops)
so they are trivially unit-testable.
"""

from __future__ import annotations

import io
import json
import logging
import pstats
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("agent.core.profiling")


@dataclass
class ProfilePlan:
    """How to execute a command under a profiler."""

    profiler: str
    """'cprofile' | 'node' | 'go'."""
    command: str
    """The rewritten command to execute."""
    profile_path: str
    """File the profiler will write (parsed afterwards)."""
    extra_profiles: dict[str, str] = field(default_factory=dict)
    """Additional profiles produced (go: mem/mutex), name -> path."""


@dataclass
class Hotspot:
    function: str
    location: str
    self_time: float = 0.0
    cum_time: float = 0.0
    calls: int = 0
    percent: float = 0.0


# ---------------------------------------------------------------------------
# Command detection / rewriting
# ---------------------------------------------------------------------------


def detect_profiler(command: str) -> str | None:
    """Return 'cprofile' | 'node' | 'go' for a command, or None if unsupported."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return None
    exe = Path(tokens[0]).name.lower().removesuffix(".exe")
    if exe in ("python", "python3", "py") or exe.startswith("python3."):
        return "cprofile"
    if exe == "node":
        return "node"
    if exe == "go" and len(tokens) > 1 and tokens[1] == "test":
        return "go"
    return None


def unsupported_guidance(command: str) -> str:
    """Actionable message for commands no profiler can wrap generically."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    exe = Path(tokens[0]).name.lower().removesuffix(".exe") if tokens else ""
    go_hint = ""
    if exe == "go" or exe.endswith(".test"):
        go_hint = (
            "\n\nFor Go binaries (`go run` / prebuilt executables) the runtime "
            "cannot be profiled from outside. Standard recipe:\n"
            "1. Create a `bench_test.go` with a `BenchmarkX` that calls the hot path.\n"
            "2. run_profiler(command=\"go test -bench=. -run=^$ -benchtime=2s .\")\n"
            "This yields CPU/memory/mutex profiles without touching source code."
        )
    return (
        f"run_profiler does not support this command shape (auto-detect found no "
        f"profilable runtime). Supported:\n"
        f"  - python <script>.py [args]      (cProfile)\n"
        f"  - node <script>.js [args]        (--cpu-prof)\n"
        f"  - go test [flags] <pkg>          (-cpuprofile/-memprofile/-mutexprofile)"
        f"{go_hint}\n"
        f"Alternative: verify_determinism(command=...) checks output stability, and "
        f"run_task(command=..., save_output_to=...) captures golden output."
    )


def build_profile_command(command: str, profiler: str, profile_dir: Path) -> ProfilePlan | None:
    """Rewrite *command* to run under *profiler*, returning the plan.

    Returns None when the profiler can't wrap this command shape.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    if profiler == "cprofile":
        out = profile_dir / "cprofile.out"
        rewritten = _rewrite_python(command, out)
        if rewritten is None:
            return None
        return ProfilePlan(profiler=profiler, command=rewritten, profile_path=str(out))
    if profiler == "node":
        # --cpu-prof writes <dir>/CPU.<timestamp>.<pid>.0.cpuprofile
        rewritten = _rewrite_node(command, profile_dir)
        if rewritten is None:
            return None
        return ProfilePlan(profiler=profiler, command=rewritten, profile_path=str(profile_dir))
    if profiler == "go":
        cpu = profile_dir / "cpu.out"
        mem = profile_dir / "mem.out"
        mutex = profile_dir / "mutex.out"
        rewritten = _rewrite_go_test(command, cpu, mem, mutex)
        if rewritten is None:
            return None
        return ProfilePlan(
            profiler=profiler,
            command=rewritten,
            profile_path=str(cpu),
            extra_profiles={"memory": str(mem), "mutex": str(mutex)},
        )
    return None


def _rewrite_python(command: str, out: Path) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    exe = tokens[0]
    rest = tokens[1:]
    # `python -m cProfile -o out ...rest`; drop a pre-existing -m flag chain.
    if rest and rest[0] == "-m":
        # Already module-invocation; wrap the whole thing anyway.
        pass
    quoted = " ".join(shlex.quote(t) for t in rest)
    return f"{exe} -m cProfile -o {shlex.quote(str(out))} {quoted}".strip()


def _rewrite_node(command: str, profile_dir: Path) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    exe = tokens[0]
    rest = " ".join(shlex.quote(t) for t in tokens[1:])
    return (
        f"{exe} --cpu-prof --cpu-prof-dir={shlex.quote(str(profile_dir))} {rest}"
    ).strip()


def _rewrite_go_test(command: str, cpu: Path, mem: Path, mutex: Path) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[1] != "test":
        return None
    flags = [
        f"-cpuprofile={cpu}",
        f"-memprofile={mem}",
        f"-mutexprofile={mutex}",
    ]
    # Insert flags right after 'go test', keep the rest untouched.
    new_tokens = [tokens[0], "test", *flags, *tokens[2:]]
    return " ".join(shlex.quote(t) for t in new_tokens)


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


def parse_cprofile(profile_path: str, top_n: int = 5) -> list[Hotspot]:
    """Parse a cProfile output file into top-N hotspots by self time."""
    try:
        stats = pstats.Stats(profile_path)
    except Exception as e:
        logger.warning("pstats failed to load %s: %s", profile_path, e)
        return []
    total = stats.total_tt or 1.0
    entries: list[Hotspot] = []
    for (file, line, func), (cc, nc, tt, ct, _callers) in stats.stats.items():
        entries.append(Hotspot(
            function=func,
            location=f"{file}:{line}",
            self_time=tt,
            cum_time=ct,
            calls=nc,
            percent=(tt / total) * 100.0,
        ))
    entries.sort(key=lambda h: h.self_time, reverse=True)
    return entries[:top_n]


def parse_cpuprofile(profile_dir: str, top_n: int = 5) -> list[Hotspot]:
    """Parse the newest .cpuprofile in *profile_dir* (Node --cpu-prof)."""
    directory = Path(profile_dir)
    candidates = sorted(directory.glob("*.cpuprofile"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return []
    try:
        data = json.loads(candidates[0].read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        logger.warning("failed to parse %s: %s", candidates[0], e)
        return []

    nodes = {n["id"]: n for n in data.get("nodes", [])}
    deltas = data.get("timeDeltas", [])
    samples = data.get("samples", [])
    self_us: dict[int, int] = {}
    for sid, delta in zip(samples, deltas):
        self_us[sid] = self_us.get(sid, 0) + int(delta)
    total_us = sum(self_us.values()) or 1

    entries: list[Hotspot] = []
    for node_id, us in self_us.items():
        node = nodes.get(node_id)
        if not node:
            continue
        cf = node.get("callFrame", {})
        name = cf.get("functionName") or "(anonymous)"
        url = cf.get("url", "")
        line = int(cf.get("lineNumber", -1)) + 1
        entries.append(Hotspot(
            function=name,
            location=f"{url}:{line}" if url else "",
            self_time=us / 1e6,
            percent=(us / total_us) * 100.0,
        ))
    entries.sort(key=lambda h: h.self_time, reverse=True)
    return entries[:top_n]


def parse_pprof_top(text: str, top_n: int = 5) -> list[Hotspot]:
    """Parse `go tool pprof -top` text output into hotspots."""
    entries: list[Hotspot] = []
    # Rows look like:  1.23s  45.6%  45.6%   2.34s  78.9%  pkg.Func
    row = re.compile(
        r"^\s*([\d.]+)(s|ms|us|ns)\s+([\d.]+)%\s+[\d.]+%\s+"
        r"([\d.]+)(s|ms|us|ns)\s+[\d.]+%\s+(\S+)\s*$"
    )
    for line in text.splitlines():
        m = row.match(line)
        if not m:
            continue
        self_s = _to_seconds(float(m.group(1)), m.group(2))
        cum_s = _to_seconds(float(m.group(4)), m.group(5))
        entries.append(Hotspot(
            function=m.group(6),
            location="",
            self_time=self_s,
            cum_time=cum_s,
            percent=float(m.group(3)),
        ))
        if len(entries) >= top_n:
            break
    return entries


def _to_seconds(value: float, unit: str) -> float:
    return {"s": value, "ms": value / 1e3, "us": value / 1e6, "ns": value / 1e9}.get(unit, value)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_hotspots(
    title: str,
    hotspots: list[Hotspot],
    *,
    extra_sections: list[str] | None = None,
    command: str = "",
) -> str:
    """Render a clean top-N bottleneck summary for the agent."""
    lines: list[str] = []
    if command:
        lines.append(f"Profiled: {command}")
    lines.append(title)
    if not hotspots:
        lines.append("(no hotspot data — the profile may be empty or the run too short)")
    else:
        header = f"{'#':>2} {'self':>9} {'%':>6} {'cum':>9}  function"
        lines.append(header)
        lines.append("-" * len(header))
        for i, h in enumerate(hotspots, 1):
            loc = f"  ({h.location})" if h.location else ""
            cum = f"{h.cum_time:.3f}s" if h.cum_time else "-"
            lines.append(
                f"{i:>2} {h.self_time:>8.3f}s {h.percent:>5.1f}% {cum:>9}  {h.function}{loc}"
            )
    for section in extra_sections or []:
        if section:
            lines.append("")
            lines.append(section)
    return "\n".join(lines)
