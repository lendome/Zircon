"""
Task handler — run a task headless (no TUI).

Streams trace events to stdout. Exits when the task completes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ._shared import resolve_tier, create_agent
from ...runtime import ParsedArgs, RuntimeContext


_PHASE_COLORS = {
    "start": "\033[92m", "plan": "\033[96m", "step": "\033[93m",
    "tool_call": "\033[95m", "tool_result": "\033[90m", "explore": "\033[94m",
    "edit": "\033[93m", "verify": "\033[91m", "done": "\033[92m",
    "task_complete": "\033[92m", "task_failed": "\033[91m",
    "awaiting_input": "\033[96m", "subagent_progress": "\033[94m",
    "advisor": "\033[96m", "advisor_checkin": "\033[96m",
}
_RESET = "\033[0m"


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    # The positional args are the task description, not a workspace path —
    # always run against the invocation directory.
    workspace = str(Path(ctx.workspace).resolve())
    tier = resolve_tier(args)
    swarm = bool(args.get("swarm"))
    plan_mode = bool(args.get("plan-mode"))

    task_desc = " ".join(args.positional) if args.positional else ""
    if not task_desc:
        print("Usage: zircon task <task description>", file=sys.stderr)
        return 1

    agent = create_agent(repo_path=workspace, tier=tier, swarm_mode=swarm, plan_mode=plan_mode)

    # Arm the approval gate for headless CLI use: destructive git/db commands
    # prompt on stdin (and are auto-denied when stdin isn't a TTY).
    from ...approval import HeadlessApprovalHandler

    agent.approval_gate.set_handler(HeadlessApprovalHandler().request)
    agent.approval_gate.enable()

    print(f"\033[1mTask:\033[0m {task_desc}")
    print(f"  \033[2mTier: {agent.tier.value}\033[0m\n")

    async for event in agent.solve_stream(task_desc):
        phase = event.phase
        color = _PHASE_COLORS.get(phase, "")
        detail = event.detail
        if event.payload and phase == "plan" and "steps" in event.payload:
            steps = event.payload["steps"]
            step_lines = [f"    {s['index']}. [{s['action']}] {s['desc']}" for s in steps]
            detail = f"{event.detail}\n" + "\n".join(step_lines)
        if event.payload and phase == "advisor" and event.payload.get("advisor_plan"):
            plan_lines = event.payload["advisor_plan"].splitlines()
            detail = "Advisor Execution Plan:\n" + "\n".join(f"    {line}" for line in plan_lines)
        if event.payload and phase == "advisor_checkin" and event.payload.get("advisor_feedback"):
            turn_no = event.payload.get("turn", "?")
            fb_lines = event.payload["advisor_feedback"].splitlines()
            detail = f"Advisor check-in (turn {turn_no}):\n" + "\n".join(f"    {line}" for line in fb_lines)
        print(f"  {color}[{phase.upper().replace('_', ' ')}]{_RESET} {detail}")

        if phase in ("task_complete", "task_failed") and event.payload.get("answer"):
            print(f"\n{event.payload['answer']}\n")

        if phase == "awaiting_input":
            print("\n  \033[96mPlan requires approval. Use the TUI to approve.\033[0m")
            return 0

    return 0
