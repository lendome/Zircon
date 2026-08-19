from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import sys
import time
from pathlib import Path

from .core.agent import Agent
from .core.types import TraceEvent, TaskStatus, Tier
from .core.constants import ensure_zircon_dir
from .core.diff_display import colorize_diff
from .core.logging_config import setup_logging


_AT_REF_RE = re.compile(r"@(\S+)")

def _show_status(msg: str) -> None:
    print(f"  --> {msg}", flush=True)


def _clear_status() -> None:
    pass


def _find_files_by_name(repo_path: Path, query: str, max_results: int = 10) -> list[Path]:
    matches: list[Path] = []
    query_lower = query.lower()
    for root, _dirs, files in os.walk(repo_path):
        rel_root = Path(root).relative_to(repo_path)
        parts = rel_root.parts
        if any(p.startswith(".") for p in parts):
            continue
        for f in files:
            if query_lower in f.lower():
                matches.append(Path(root) / f)
                if len(matches) >= max_results:
                    return matches
    return matches


def _resolve_at_refs(repo_path: Path, text: str) -> str:
    refs = _AT_REF_RE.findall(text)
    if not refs:
        return text

    resolved_parts: list[str] = []
    for ref in refs:
        candidates = _find_files_by_name(repo_path, ref, max_results=10)
        if not candidates:
            print(f"  \033[2m@ref: no files matching '{ref}' found.\033[0m")
            continue

        chosen: Path | None = None
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            top3 = candidates[:3]
            print(f"  \033[96m@{ref} matches:\033[0m")
            for i, p in enumerate(top3, 1):
                print(f"    {i}. {p.relative_to(repo_path)}")
            if len(candidates) > 3:
                print(f"    ... and {len(candidates) - 3} more")
            try:
                choice = input(f"  Pick (1-{len(top3)}), or Enter to skip: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(top3):
                    chosen = top3[idx]
            else:
                continue

        if chosen is None:
            continue

        try:
            content = chosen.read_text(encoding="utf-8", errors="replace")
            limit = 8000
            truncated = len(content) > limit
            display = content[:limit]
            truncated_marker = "\n... (truncated)" if truncated else ""
            resolved_parts.append(
                f"\n--- File: {chosen.relative_to(repo_path)} ---\n{display}"
                f"{truncated_marker}\n--- End ---\n"
            )
            print(f"  \033[2mInjected @{ref} -> {chosen.relative_to(repo_path)}\033[0m")
        except Exception as exc:
            print(f"  \033[2mCould not read {chosen}: {exc}\033[0m")

    cleaned_text = _AT_REF_RE.sub(r"\1", text)
    if resolved_parts:
        cleaned_text += "\n\n[Referenced files context]\n" + "\n".join(resolved_parts)
    return cleaned_text


PHASE_COLORS = {  # you can change these if you want i guess
    "start": "\033[92m",
    "plan": "\033[96m",
    "step": "\033[93m",
    "tool_call": "\033[95m",
    "tool_result": "\033[90m",
    "explore": "\033[94m",
    "edit": "\033[93m",
    "verify": "\033[91m",
    "replan": "\033[91m",
    "verify_failed": "\033[91m",
    "done": "\033[92m",
    "subagent": "\033[94m",
    "awaiting_input": "\033[96m",
    "task_complete": "\033[92m",
    "task_failed": "\033[91m",
    "reflect": "\033[94m",
}
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[96m"
PURPLE = "\033[95m"
DIM = "\033[2m"


def fmt_event(event: TraceEvent) -> str:
    color = PHASE_COLORS.get(event.phase, "")
    phase = event.phase.upper().replace("_", " ")
    detail = event.detail

    if event.payload:
        if event.phase == "plan" and "steps" in event.payload:
            steps = event.payload["steps"]
            step_lines = [f"    {s['index']}. [{s['action']}] {s['desc']}" for s in steps]
            detail = f"{event.detail}\n" + "\n".join(step_lines)
        elif event.phase == "awaiting_input" and event.payload.get("plan"):
            plan = event.payload["plan"]
            lines = [f"    Complexity: {plan['complexity']}"]
            for s in plan["steps"]:
                lines.append(f"    {s['index']}. [{s['action']}] {s['desc']}")
                if s.get("targets"):
                    lines.append(f"        -> {', '.join(s['targets'])}")
            detail = f"{event.detail}\n" + "\n".join(lines)
        elif event.phase == "tool_call" and event.payload.get("result_preview"):
            preview = event.payload["result_preview"][:150].replace("\n", " ")
            detail = f"{detail}\n    -> {preview}"
        elif event.phase in ("task_complete", "done") and event.payload.get("answer"):
            detail = event.payload["answer"][:200]

    return f"  {color}[{phase}]{RESET} {detail}"


async def run_task_mode(agent: Agent, task: str):
    print(f"\n{BOLD}Task:{RESET} {task}")
    print(f"  {DIM}Tier: {agent.tier.value}{RESET}\n")

    while True:
        hit_awaiting = False
        async for event in agent.solve_stream(task):
            _clear_status()
            print(fmt_event(event))
            if event.phase == "awaiting_input":
                hit_awaiting = True
                break
        else:
            print()
            return

        if hit_awaiting and agent.status == TaskStatus.AWAITING_INPUT:
            _clear_status()
            if agent.pending_plan:
                plan = agent.pending_plan
                print(f"\n  {CYAN}--- Proposed Plan ---{RESET}")
                print(f"    Complexity: {plan.complexity}")
                for s in plan.steps:
                    print(f"    {s.index}. [{s.action}] {s.description}")
                    if s.target_files:
                        print(f"        -> {', '.join(s.target_files)}")
                print()
                try:
                    feedback = input(f"  {BOLD}Approve? [Y/n/edit] >{RESET} ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return
                if feedback.lower() in ("", "y", "yes"):
                    agent.submit_feedback("approved")
                else:
                    agent.submit_feedback(feedback)
            else:
                try:
                    feedback = input(f"  {BOLD}Provide guidance >{RESET} ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return
                agent.submit_feedback(feedback)
        else:
            print()
            return


async def _stream_chat(agent: Agent, message: str):
    thinking_active = False
    tokens_used = 0
    session_cost = 0.0
    buf = ""

    _show_status("Waiting for LLM response\u2026")

    async for chunk in agent.chat_stream(message):
        if chunk.progress_label:
            _show_status(chunk.progress_label)
            continue  # Progress-only chunks have no other content

        if chunk.status == TaskStatus.AWAITING_INPUT:
            _clear_status()
            if chunk.text:
                sys.stdout.write(f"\n{chunk.text}\n")
            if agent.pending_plan:
                sys.stdout.write(f"\n  {CYAN}Awaiting your approval.{RESET}\n")
            else:
                sys.stdout.write(f"\n  {CYAN}Awaiting your guidance.{RESET}\n")
            sys.stdout.flush()
            return

        if chunk.status == TaskStatus.COMPLETED and chunk.done:
            _clear_status()
            sys.stdout.write(f"\n  {BOLD}Done.{RESET}\n")
            if tokens_used:
                sys.stdout.write(f"  {DIM}({tokens_used} tokens){RESET}\n")
            if session_cost:
                sys.stdout.write(f"  {DIM}(session cost: ${session_cost:.4f}){RESET}\n")
            sys.stdout.flush()
            return

        if chunk.status == TaskStatus.FAILED and chunk.done:
            _clear_status()
            sys.stdout.write(f"\n  {BOLD}Failed.{RESET}\n")
            if chunk.error:
                sys.stdout.write(f"  \033[91m{chunk.error}\033[0m\n")
            if tokens_used:
                sys.stdout.write(f"  {DIM}({tokens_used} tokens){RESET}\n")
            if session_cost:
                sys.stdout.write(f"  {DIM}(session cost: ${session_cost:.4f}){RESET}\n")
            sys.stdout.flush()
            return

        if chunk.reasoning:
            if not thinking_active:
                _clear_status()
                sys.stdout.write(f"  {CYAN}Thinking...{RESET}")
                sys.stdout.flush()
                thinking_active = True
            continue

        if chunk.text:
            if thinking_active:
                sys.stdout.write("\r" + " " * 40 + "\r")
                thinking_active = False
            _clear_status()

            buf += chunk.text
            while True:
                if thinking_active:
                    end = re.search(r'</(?:thinking|think)>', buf)
                    if end:
                        buf = buf[end.end():]
                        thinking_active = False
                        sys.stdout.write("\r" + " " * 40 + "\r")
                        sys.stdout.flush()
                        continue
                else:
                    start = re.search(r'<(?:thinking|think)(?:\s[^>]*)?>', buf)
                    if start:
                        sys.stdout.write(buf[:start.start()])
                        sys.stdout.flush()
                        buf = buf[start.end():]
                        thinking_active = True
                        sys.stdout.write(f"  {CYAN}Thinking...{RESET}")
                        sys.stdout.flush()
                        continue
                break

            if not thinking_active and buf:
                safe = len(buf)
                lt = buf.rfind('<')
                if lt >= 0 and lt >= len(buf) - 12:
                    safe = lt
                sys.stdout.write(buf[:safe])
                sys.stdout.flush()
                buf = buf[safe:]

        elif chunk.tool_calls:
            if thinking_active:
                sys.stdout.write("\r" + " " * 40 + "\r")
                thinking_active = False
            _clear_status()
            for tc in chunk.tool_calls:
                args_summary = ""
                if tc.arguments:
                    args_preview = str(tc.arguments)[:120]
                    args_summary = f" {DIM}{args_preview}{RESET}"
                sys.stdout.write(f"\n  {PURPLE}[TOOL] {tc.name}{RESET}{args_summary}\n")
                sys.stdout.flush()
            _show_status("Executing tool...")

        elif chunk.tool_result:
            if thinking_active:
                sys.stdout.write("\r" + " " * 40 + "\r")
                thinking_active = False
            _clear_status()
            result_text = chunk.tool_result
            if "--- a/" in result_text or "+++ b/" in result_text:
                sys.stdout.write(colorize_diff(result_text) + "\n")
            else:
                preview = result_text[:200].replace("\n", " ")
                sys.stdout.write(f"  {DIM}{preview}{RESET}\n")
            sys.stdout.flush()
            _show_status("Waiting for LLM response (tool result sent)")

        elif chunk.error:
            _clear_status()
            if thinking_active:
                sys.stdout.write("\r" + " " * 40 + "\r")
                thinking_active = False
            sys.stdout.write(f"\n  \033[91mError: {chunk.error}\033[0m\n")
            sys.stdout.flush()

        elif chunk.done and chunk.usage:
            tokens_used = chunk.usage.get("total_tokens", tokens_used)
            session_cost = getattr(getattr(agent, "router", None), "session_cost_usd", session_cost)

    if buf and not thinking_active:
        sys.stdout.write(buf)
    if thinking_active:
        sys.stdout.write("\r" + " " * 40 + "\r")
    _clear_status()
    sys.stdout.write("\n")
    if tokens_used:
        sys.stdout.write(f"  {DIM}({tokens_used} tokens){RESET}\n")
    if session_cost:
        sys.stdout.write(f"  {DIM}(session cost: ${session_cost:.4f}){RESET}\n")
    sys.stdout.flush()


async def run_chat_mode(agent: Agent):
    print(f"\n{BOLD}zircon \u2014 Interactive Chat{RESET}")
    print(f"Repo: {agent.repo_path}")
    print(f"Tier: {agent.tier.value}")
    print("Commands: /task <desc>, /approve, /reset, /status, /help, /exit\n")

    while True:
        try:
            user_input = input(f"{BOLD}>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            break
        elif user_input == "/reset":
            agent.context.clear_history()
            print("  Context cleared.\n")
            continue
        elif user_input == "/status":
            print(f"  Working set: {len(agent.context.working_set)} files")
            print(f"  Modified: {len(agent.context.modified_files)} files")
            print(f"  Notes: {len(agent.context.session_notes)}")
            if agent.context.modified_files:
                print(f"  Files: {', '.join(sorted(agent.context.modified_files))}")
            print()
            continue
        elif user_input == "/help":
            print("  /task <desc>  \u2014 Run a full agent task (plan + execute)")
            print("  /approve      \u2014 Approve a pending plan and continue")
            print("  /reset        \u2014 Clear conversation context")
            print("  /status       \u2014 Show working set and modified files")
            print("  /exit         \u2014 Quit")
            print("  <message>     \u2014 Chat with tool use")
            print("  @<file>       \u2014 Reference a file (with autocomplete)\n")
            continue
        elif user_input == "/approve":
            if agent.status == TaskStatus.AWAITING_INPUT:
                agent.submit_feedback("approved")
                await _stream_chat(agent, "")
            else:
                print("  No plan awaiting approval.\n")
            continue
        elif user_input.startswith("/task "):
            task = user_input[6:].strip()
            if not task:
                print("  Usage: /task <task description>\n")
                continue
            task = _resolve_at_refs(agent.repo_path, task)
            await run_task_mode(agent, task)
            continue

        user_input = _resolve_at_refs(agent.repo_path, user_input)

        if agent.status == TaskStatus.AWAITING_INPUT:
            agent.submit_feedback(user_input)
            await _stream_chat(agent, user_input)
            continue

        await _stream_chat(agent, user_input)
        print()


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    tier = Tier.BALANCED
    plan_mode = "--plan-mode" in args
    swarm_mode = "--swarm" in args
    dump_context = "--dump-context" in args
    if "--low" in args:
        tier = Tier.LOW
        args = [a for a in args if a != "--low"]
    elif "--quality" in args:
        tier = Tier.QUALITY
        args = [a for a in args if a != "--quality"]

    # Remove consumed flags (keep --plan-mode out of repo path)
    args = [a for a in args if a not in ("--verbose", "-v", "--swarm", "--dump-context", "--plan-mode")]

    repo_path = args[0] if args else "."
    repo_path = str(Path(repo_path).resolve())

    print(f"  {CYAN}Initializing workspace...{RESET}")
    ensure_zircon_dir(repo_path)

    log_file = setup_logging(repo_path, console=verbose)

    config_path = str(Path(__file__).parent / "models.yaml")

    print(f"  {CYAN}Starting agent...{RESET}")
    agent = Agent(repo_path=repo_path, config_path=config_path, tier=tier, swarm_mode=swarm_mode, dump_context=dump_context, plan_mode=plan_mode)

    if len(args) > 1:
        task = " ".join(args[1:])
        asyncio.run(run_task_mode(agent, task))
    else:
        asyncio.run(run_chat_mode(agent))


if __name__ == "__main__":
    main()
