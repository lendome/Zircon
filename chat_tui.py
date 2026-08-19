"""
Rich-powered async TUI for Zircon agent chat.

Single-threaded, single-event-loop design. Everything runs inside
asyncio.run(main_async()) — no threads, no queue polling.

Usage:
  python chat_tui.py                          # CWD as workspace
  python chat_tui.py /path/to/project         # specific path
  python -m zirconAgent.chat_tui              # via the package
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# ── Suppress noisy runtime warnings ──────────────────────────────────────────
warnings.filterwarnings("ignore", message="coroutine 'wait_for' was never awaited")
warnings.filterwarnings("ignore", message="Task was destroyed but it is pending")
warnings.filterwarnings("ignore", message="coroutine method 'aclose'")

# ── Bulletproof imports ──────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
if (_HERE / "models.yaml").exists():
    _PROJECT_ROOT = _HERE
elif (_HERE.parent / "models.yaml").exists():
    _PROJECT_ROOT = _HERE.parent
else:
    _PROJECT_ROOT = _HERE

for _p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT.parent)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import zirconAgent.core.agent as _agent_mod  # type: ignore[import-not-found]
    import zirconAgent.core.types as _types_mod  # type: ignore[import-not-found]
    import zirconAgent.core.constants as _const_mod  # type: ignore[import-not-found]
    import zirconAgent.core.logging_config as _log_mod  # type: ignore[import-not-found]
except ImportError:
    import core.agent as _agent_mod          # type: ignore[no-redef]
    import core.types as _types_mod          # type: ignore[no-redef]
    import core.constants as _const_mod      # type: ignore[no-redef]
    import core.logging_config as _log_mod   # type: ignore[no-redef]

Agent = _agent_mod.Agent
StreamChunk = _types_mod.StreamChunk
TaskStatus = _types_mod.TaskStatus
Tier = _types_mod.Tier
TraceEvent = _types_mod.TraceEvent
ensure_zircon_dir = _const_mod.ensure_zircon_dir
setup_logging = _log_mod.setup_logging

# ── Rich imports ─────────────────────────────────────────────────────────────
from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

console = Console()


# ── Text helpers ──────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> tuple[str, list[str]]:
    import re
    thoughts: list[str] = []
    cleaned = re.sub(
        r'<(?:thinking|think)(?:\s[^>]*)?>(.*?)</(?:thinking|think)>',
        lambda m: thoughts.append(m.group(1).strip()) or "",
        text,
        flags=re.DOTALL,
    )
    return cleaned.strip(), thoughts


def _ellide(s: str, max_len: int = 200) -> str:
    return s if len(s) <= max_len else s[:max_len] + "…"


@dataclass
class UserInput:
    """Raw user input plus the compact form already shown in the terminal."""

    content: str
    display: str


@dataclass
class _InputSegment:
    content: str
    display: str


@dataclass
class _InputRenderState:
    suggestion_lines: int = 0


_BRACKETED_PASTE_START = "\x1b[200~"
_BRACKETED_PASTE_END = "\x1b[201~"
_PASTE_BURST_MIN_CHARS = 8
_PASTE_BURST_MIN_MULTILINE_CHARS = 4
_PASTE_IDLE_SECONDS = 0.015
_PASTE_PLACEHOLDER_THRESHOLD = 1000
_INPUT_PROMPT = "> "
_MAX_COMMAND_SUGGESTIONS = 5
_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show this help"),
    ("/approve", "Approve a pending plan"),
    ("/task", "Run a full agent task"),
    ("/reset", "Clear context"),
    ("/status", "Show workspace state"),
    ("/tier", "Display tier"),
    ("/warnings", "Toggle internal warnings"),
    ("/exit", "Quit"),
]


_ASCII_ART = [
    "  ______  ___  ____   ____  ___   _   _ ",
    " |__  / |_ _||  _ \\ / ___|/ _ \\ | \\ | |",
    "   / /   | | | |_) | |   | | | ||  \\| |",
    "  / /_   | | |  _ <| |___| |_| || |\\  |",
    " /____| |___||_| \\_\\\\____|\\___/ |_| \\_|",
]


def _line_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text.splitlines()))


def _paste_placeholder(index: int, text: str) -> str:
    line_count = _line_count(text)
    if len(text) >= _PASTE_PLACEHOLDER_THRESHOLD:
        noun = "line" if line_count == 1 else "lines"
        return f"[pasted text {line_count} {noun}]"
    noun = "line" if line_count == 1 else "lines"
    return f"[Pasted text #{index} +{line_count} {noun}]"


def _looks_like_paste(text: str) -> bool:
    """Heuristic: multi-char, or contains newlines, or long-enough single burst."""
    if len(text) >= _PASTE_BURST_MIN_CHARS:
        return True
    if ("\n" in text or "\r" in text) and len(text) >= _PASTE_BURST_MIN_MULTILINE_CHARS:
        return True
    return False


def _compact_input_display(text: str, paste_counter: list[int]) -> str:
    if _looks_like_paste(text):
        paste_counter[0] += 1
        return _paste_placeholder(paste_counter[0], text)
    return text


def _input_display(segments: list[_InputSegment]) -> str:
    return "".join(segment.display for segment in segments)


def _terminal_width() -> int:
    return max(40, console.size.width)


def _truncate_display(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return "." * width
    return "..." + text[-(width - 3):]


def _visible_input_display(display: str, terminal_width: int) -> str:
    width = terminal_width - len(_INPUT_PROMPT) - 1
    return _truncate_display(display, width)


def _command_suggestions(display: str) -> list[tuple[str, str]]:
    if not display.startswith("/"):
        return []

    parts = display.split(maxsplit=1)
    command_prefix = parts[0].lower()
    if len(parts) > 1 and any(command_prefix == command for command, _ in _COMMANDS):
        return []

    return [
        (command, description)
        for command, description in _COMMANDS
        if command.startswith(command_prefix)
    ]


def _format_suggestion_rows(display: str, terminal_width: int) -> list[str]:
    suggestions = _command_suggestions(display)
    if not suggestions:
        return []

    shown = suggestions[:_MAX_COMMAND_SUGGESTIONS]
    rows = [
        _truncate_display(f"  {command:<10} {description}", terminal_width - 1)
        for command, description in shown
    ]
    remaining = len(suggestions) - len(shown)
    if remaining > 0:
        rows.append(_truncate_display(f"  ... {remaining} more command(s)", terminal_width - 1))
    return rows


def _render_input_line(
    segments: list[_InputSegment],
    state: _InputRenderState,
) -> None:
    display = _input_display(segments)
    terminal_width = _terminal_width()
    visible_display = _visible_input_display(display, terminal_width)
    suggestion_rows = _format_suggestion_rows(display, terminal_width)
    rows_to_clear = max(state.suggestion_lines, len(suggestion_rows))

    sys.stdout.write(f"\r\x1b[2K{_INPUT_PROMPT}{visible_display}")
    for i in range(rows_to_clear):
        row = suggestion_rows[i] if i < len(suggestion_rows) else ""
        sys.stdout.write(f"\n\r\x1b[2K{row}")
    if rows_to_clear:
        sys.stdout.write(f"\x1b[{rows_to_clear}A")
    sys.stdout.write(f"\r{_INPUT_PROMPT}{visible_display}")
    sys.stdout.flush()
    state.suggestion_lines = len(suggestion_rows)


def _finish_input_line(state: _InputRenderState) -> None:
    # Clear the preview block and leave the cursor below the prompt.
    if state.suggestion_lines:
        for _ in range(state.suggestion_lines):
            sys.stdout.write("\n\r\x1b[2K")
        state.suggestion_lines = 0
    sys.stdout.write("\n")
    sys.stdout.flush()


def _append_paste_segment(
    segments: list[_InputSegment],
    text: str,
    paste_counter: list[int],
) -> None:
    if not text:
        return
    paste_counter[0] += 1
    display = _paste_placeholder(paste_counter[0], text)
    segments.append(_InputSegment(text, display))


def _append_normal_char(segments: list[_InputSegment], ch: str) -> None:
    segments.append(_InputSegment(ch, ch))


def _read_until_paste_end(
    initial: str,
    read_char: Callable[[], str],
) -> str:
    text = initial
    while _BRACKETED_PASTE_END not in text:
        text += read_char()
    pasted, _rest = text.split(_BRACKETED_PASTE_END, 1)
    return pasted


def _has_newline(text: str) -> bool:
    return "\n" in text or "\r" in text


def _accumulate_all_available(
    read_char: Callable[[], str],
    read_available: Callable[[], str],
    initial: str,
    timeout: float = 0.05,
) -> str:
    """Accumulate all characters arriving within `timeout` seconds."""
    parts = [initial]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        more = read_available()
        if more:
            parts.append(more)
            deadline = time.monotonic() + timeout  # reset deadline on each batch
        else:
            time.sleep(0.001)
    return "".join(parts)


def _read_user_input_with_backend(
    read_char: Callable[[], str],
    read_available: Callable[[], str],
    paste_counter: list[int],
) -> UserInput:
    segments: list[_InputSegment] = []
    render_state = _InputRenderState()
    _render_input_line(segments, render_state)

    while True:
        ch = read_char()
        if not ch:
            continue

        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":
            raise EOFError

        # ── Bracketed paste: read everything until the end marker ────
        if ch == "\x1b":
            seq = ch + read_available()
            if seq.startswith(_BRACKETED_PASTE_START):
                initial = seq[len(_BRACKETED_PASTE_START):]
                pasted = _read_until_paste_end(initial, read_char)
                _append_paste_segment(segments, pasted, paste_counter)
                _render_input_line(segments, render_state)
            continue

        # ── Accumulate all currently available data ──────────────────
        batch = _accumulate_all_available(read_char, read_available, ch, timeout=0.05)

        # A lone Enter (single \r or \n) → submit the input
        if batch in ("\r", "\n"):
            _finish_input_line(render_state)
            content = "".join(segment.content for segment in segments)
            display = "".join(segment.display for segment in segments)
            return UserInput(content=content, display=display)

        # Multi-character batch with newlines, or any batch > 3 chars → paste
        if _has_newline(batch) or len(batch) > 3:
            _append_paste_segment(segments, batch, paste_counter)
            _render_input_line(segments, render_state)
            continue

        # ── Single non-enter character(s): process them ──────────────
        for c in batch:
            if c in ("\b", "\x7f"):
                if segments:
                    segments.pop()
                    _render_input_line(segments, render_state)
                continue
            if c == "\x03":
                raise KeyboardInterrupt
            if c == "\x04":
                raise EOFError
            if c >= " " or c == "\t":
                _append_normal_char(segments, c)
                _render_input_line(segments, render_state)


def _read_available_windows() -> str:
    import msvcrt

    chars: list[str] = []
    deadline = time.monotonic() + _PASTE_IDLE_SECONDS
    while True:
        while msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    msvcrt.getwch()
                continue
            chars.append(ch)
            deadline = time.monotonic() + _PASTE_IDLE_SECONDS
        if time.monotonic() >= deadline:
            break
        time.sleep(0.001)
    return "".join(chars)


def _read_user_input_windows(paste_counter: list[int]) -> UserInput:
    import msvcrt

    def read_char() -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            return ""
        return ch

    return _read_user_input_with_backend(read_char, _read_available_windows, paste_counter)


def _read_available_posix() -> str:
    import select

    chars: list[str] = []
    deadline = time.monotonic() + _PASTE_IDLE_SECONDS
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            break
        chars.append(sys.stdin.read(1))
        deadline = time.monotonic() + _PASTE_IDLE_SECONDS
    return "".join(chars)


def _read_user_input_posix(paste_counter: list[int]) -> UserInput:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _read_user_input_with_backend(lambda: sys.stdin.read(1), _read_available_posix, paste_counter)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _read_user_input(paste_counter: list[int]) -> UserInput:
    # Enable bracketed paste in terminals that support it. Burst detection below
    # still handles Windows terminals that do not send paste boundary markers.
    bracketed_paste_enabled = False
    if sys.stdin.isatty() and sys.stdout.isatty():
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        bracketed_paste_enabled = True

    try:
        if sys.stdin.isatty():
            if sys.platform == "win32":
                return _read_user_input_windows(paste_counter)
            return _read_user_input_posix(paste_counter)

        raw = sys.stdin.readline()
        if raw == "":
            raise EOFError
        raw = raw.rstrip("\r\n")
        return UserInput(raw, _compact_input_display(raw, paste_counter))
    finally:
        if bracketed_paste_enabled:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()


def _format_tool_args(name: str, args: dict | None, max_len: int = 120) -> str:
    if not args:
        return name
    preview = str(args)
    if len(preview) > max_len:
        preview = preview[:max_len] + "…"
    return f"{name}({preview})"


def _render_diff(diff_text: str) -> Panel:
    syntax = Syntax(diff_text, "diff", theme="ansi_dark", line_numbers=False, word_wrap=True)
    return Panel(syntax, title="Diff", border_style="yellow")


def _print_startup_screen(repo_path: str, tier: Tier) -> None:
    console.clear()
    banner_height = len(_ASCII_ART) + 7
    top_padding = max(1, (console.height - banner_height) // 2)
    if top_padding:
        console.print("\n" * top_padding, end="")

    console.print(Align.center(Text("\n".join(_ASCII_ART), style="bold green")))
    console.print(Align.center(Text("Autonomous Coding Agent", style="cyan")))
    console.print()
    console.print(Align.center(Text(f"Workspace: {repo_path}", style="dim")))
    console.print(Align.center(Text(f"Tier: {tier.value}", style="dim")))
    console.print()
    console.print(
        Align.center(
            Text("Type / to browse commands. Type to send your prompt.", style="dim")
        )
    )
    console.print()


# ── Text buffer ──────────────────────────────────────────────────────────────

class TextBuffer:
    """Accumulates streaming text chunks and flushes on word/sentence boundaries."""

    def __init__(self) -> None:
        self._buf = ""

    def append(self, text: str) -> None:
        self._buf += text

    def flushable(self) -> bool:
        b = self._buf
        if len(b) < 5:
            return False
        for sep in (". ", "! ", "? ", ".\n", ":\n", "\n\n"):
            idx = b.rfind(sep)
            if idx > 0 and idx >= len(b) // 3:
                return True
        if len(b) >= 6 and b.endswith("."):
            return True
        half = len(b) // 2
        nl = b.find("\n", half)
        if nl > 0:
            return True
        if len(b) > 120:
            return True
        return False

    def flush(self) -> str:
        part = self._buf
        self._buf = ""
        return part

    def drain(self) -> str:
        return self.flush()

    @property
    def content(self) -> str:
        return self._buf


# ── Chunk/event rendering ────────────────────────────────────────────────────

def _chunk_to_rich(
    chunk: Any,
    text_buf: TextBuffer | None = None,
) -> list[Any]:
    """Convert a StreamChunk to 0+ Rich renderables.

    Reasoning chunks are NOT handled here — `_stream_chat` renders them as a
    single live-updating Panel via Rich Live. If a reasoning chunk reaches
    this function it is ignored (returns nothing).
    """
    out: list[Any] = []

    if chunk.progress_label and not chunk.text and not chunk.tool_calls:
        out.append(Text(f"  {chunk.progress_label}", style="dim italic"))
        return out

    if chunk.status == TaskStatus.AWAITING_INPUT:
        text = chunk.text or "Agent requires your approval."
        out.append(Panel(Text(text), title="Plan", border_style="cyan"))
        out.append(Text("  Type /approve to continue, or give feedback.", style="cyan italic"))
        return out

    if chunk.status == TaskStatus.FAILED and chunk.done:
        err = chunk.error or chunk.text or "Unknown error"
        out.append(Text(f"  {err}", style="bold red"))
        return out

    if chunk.status == TaskStatus.COMPLETED and chunk.done:
        text = chunk.text or ""
        if text:
            clean, thoughts = _strip_thinking(text)
            for t in thoughts:
                out.append(Panel(Text(t, style="grey"), title="Thought", border_style="grey", box=box.SQUARE))
            if clean:
                if text_buf and text_buf.content:
                    prev = text_buf.drain()
                    if prev.strip():
                        out.append(Markdown(prev.strip()))
                out.append(Markdown(clean))
        out.append(Text("Done.", style="bold green"))
        return out

    if chunk.reasoning:
        return []

    if chunk.text:
        if text_buf is not None:
            text_buf.append(chunk.text)
            if text_buf.flushable():
                part = text_buf.flush()
                if part.strip():
                    out.append(Markdown(part.strip()))
        else:
            clean, thoughts = _strip_thinking(chunk.text)
            for t in thoughts:
                out.append(Panel(Text(t, style="grey"), title="Thought", border_style="grey", box=box.SQUARE))
            if clean:
                out.append(Markdown(clean))
        return out

    if chunk.tool_calls:
        rows = []
        for tc in chunk.tool_calls:
            label = _format_tool_args(tc.name, tc.arguments)
            rows.append(f"{tc.name} {label}".rstrip())
        rows.append("Executing...")
        out.append(
            Panel(
                Text("\n".join(rows), style="grey"),
                title="Tool Call",
                border_style="grey",
                box=box.SQUARE,
            )
        )

    if chunk.tool_result:
        text = chunk.tool_result
        if "--- a/" in text or "+++ b/" in text or "diff --git" in text:
            out.append(_render_diff(text))
        else:
            out.append(Panel(_ellide(text, 300), title="Tool Result", border_style="grey", box=box.SQUARE))

    if chunk.error:
        out.append(Text(f"  {chunk.error}", style="bold yellow"))

    return out


def _trace_to_rich(event: Any) -> list[Any]:
    phase, detail = event.phase, event.detail
    m = {"awaiting_input": ("Plan Required", "cyan"),
         "task_complete": (None, "bold green"),
         "done": (None, "bold green"),
         "task_failed": (None, "bold red"),
         "plan": ("Plan", "cyan"),
         "step_complete": (None, "green"),
         "step_failed": (None, "red")}
    if phase in m:
        title, style = m[phase]
        tag = f"  " if not title else ""
        if title:
            return [Panel(Text(detail), title=title, border_style=style)]
        return [Text(f"  {detail}", style=style)]
    if phase == "subagent_progress":
        agent_id = (event.payload or {}).get("agent_id", "agent")
        phase_desc = (event.payload or {}).get("phase", "")
        event_detail = (event.payload or {}).get("detail", "")
        if event_detail:
            return [Text(f"  >> [{agent_id}] {event_detail}", style="dim cyan")]
        elif phase_desc:
            return [Text(f"  >> [{agent_id}] {phase_desc}...", style="dim cyan")]
        return [Text(f"  >> [{agent_id}] {detail}", style="dim cyan")]
    if phase in ("status", "start"):
        return [Text(f"  {detail}", style="dim italic")]
    return [Text(f"  [{phase}] {detail}", style="dim")]


# ── Async chat ───────────────────────────────────────────────────────────────

async def _stream_chat(agent: Agent, message: str, text_buf: TextBuffer) -> bool:
    """Stream chat, print incrementally. Returns True if plan was requested."""
    # Reasoning is rendered as a SINGLE live-updating Panel: each reasoning
    # token grows the same Panel in place (via Rich Live), instead of either
    # (a) one Panel per token, or (b) silently accumulating and dumping the
    # whole block only at completion. This gives live token streaming within
    # one container.
    reasoning_acc = ""
    reasoning_live: Live | None = None

    def _close_reasoning_live() -> None:
        nonlocal reasoning_live, reasoning_acc
        if reasoning_live is not None:
            reasoning_live.stop()
            reasoning_live = None
        reasoning_acc = ""

    try:
        async for chunk in agent.chat_stream(message):
            if chunk.reasoning:
                reasoning_acc += chunk.reasoning
                panel = Panel(
                    Text(reasoning_acc, style="grey"),
                    title="Reasoning",
                    border_style="grey",
                    box=box.SQUARE,
                )
                if reasoning_live is None:
                    reasoning_live = Live(
                        panel, console=console, refresh_per_second=12, transient=False
                    )
                    reasoning_live.start()
                else:
                    reasoning_live.update(panel)
                continue

            # Any non-reasoning chunk: finalize the reasoning panel first so
            # subsequent output renders below it (not overwritten by the Live).
            _close_reasoning_live()

            if chunk.status == TaskStatus.AWAITING_INPUT and (chunk.done or True):
                for r in _chunk_to_rich(chunk, text_buf):
                    console.print(r)
                return True
            for r in _chunk_to_rich(chunk, text_buf):
                console.print(r)
            if chunk.done:
                break
    finally:
        _close_reasoning_live()

    remaining = text_buf.drain()
    if remaining.strip():
        console.print(Markdown(remaining.strip()))
    return False


async def _stream_task(agent: Agent, task: str, text_buf: TextBuffer) -> bool:
    """Stream task, print incrementally. Returns True if plan was requested."""
    async for event in agent.solve_stream(task):
        if event.phase == "awaiting_input":
            for r in _trace_to_rich(event):
                console.print(r)
            return True
        for r in _trace_to_rich(event):
            console.print(r)
    remaining = text_buf.drain()
    if remaining.strip():
        console.print(Markdown(remaining.strip()))
    return False


# ── Async main ───────────────────────────────────────────────────────────────

async def main_async(agent: Agent) -> None:
    """Async REPL — everything runs on the same event loop."""
    last_streamed = False
    paste_counter = [0]

    while True:
        try:
            entered = _read_user_input(paste_counter)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold yellow]Bye.[/]")
            break

        user_input = entered.content
        display_input = entered.display

        if not user_input:
            continue

        # ── Slash commands ───────────────────────────────────────────────
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/exit":
                break
            elif cmd == "/help":
                command_lines = [
                    f"  [bold]{command}[/]".ljust(28) + description
                    for command, description in _COMMANDS
                ]
                console.print(Panel(
                    "\n".join(command_lines + ["", "  [dim]<any message>  Chat with agent + tool use[/]"]),
                    title="Help",
                    border_style="cyan",
                ))
                continue
            elif cmd == "/approve":
                if agent.status == TaskStatus.AWAITING_INPUT:
                    console.print("[bold green]Plan approved, continuing...[/]")
                    agent.submit_feedback("approved")
                    buf = TextBuffer()
                    await _stream_chat(agent, "", buf)
                else:
                    console.print("[yellow]No plan awaiting approval.[/]")
                continue
            elif cmd == "/reset":
                agent.context.clear_history()
                console.print("[green]Context cleared.[/]")
                continue
            elif cmd == "/status":
                ctx = agent.context
                modified = len(ctx.modified_files)
                console.print(Panel(
                    f"Working set: [cyan]{len(ctx.working_set)}[/] files\n"
                    f"Modified:    [yellow]{modified}[/] files\n"
                    f"Notes:       {len(ctx.session_notes)}\n"
                    f"History:     {len(ctx.history)} messages\n"
                    f"Status:      {agent.status.value}",
                    title="Status",
                    border_style="blue",
                ))
                continue
            elif cmd == "/tier":
                console.print("[dim]Tier is set at agent init. Restart with --low or --quality to change.[/]")
                continue
            elif cmd == "/warnings":
                active = warnings.filters[0].action != "ignore"
                if active:
                    warnings.filterwarnings("ignore")
                else:
                    warnings.filterwarnings("default")
                continue
            elif cmd == "/task":
                if not arg:
                    console.print("[yellow]Usage: /task <task description>[/]")
                    continue
                display_parts = display_input.split(maxsplit=1)
                display_arg = display_parts[1] if len(display_parts) > 1 else arg
                console.print(Text.assemble(("Task: ", "bold yellow"), display_arg))
                buf = TextBuffer()
                await _stream_task(agent, arg, buf)
                continue
            else:
                console.print(f"[yellow]Unknown command: {cmd}. Type /help.[/]")
                continue

        # ── Normal chat ─────────────────────────────────────────────────
        console.print(Text(f"  You: {display_input}", style="bold green"))
        buf = TextBuffer()
        await _stream_chat(agent, user_input, buf)
        # Clear spinner line if any
        console.print(" " * 80, end="\r")


# ── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zircon Agent — Rich TUI chat interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", default=".", help="Workspace directory")
    parser.add_argument("--low", action="store_true", help="Low tier")
    parser.add_argument("--quality", action="store_true", help="Quality tier")
    parser.add_argument("--plan-mode", action="store_true", help="Enable planning (disabled by default)")
    parser.add_argument("--swarm", action="store_true", help="Swarm mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--warnings", action="store_true", help="Show internal warnings")
    args = parser.parse_args()

    tier = Tier.BALANCED
    if args.low:
        tier = Tier.LOW
    elif args.quality:
        tier = Tier.QUALITY

    repo_path = str(Path(args.path).resolve())
    if not Path(repo_path).is_dir():
        console.print(f"[bold red]Error:[/] directory not found: {repo_path}")
        sys.exit(1)

    if args.warnings:
        warnings.filterwarnings("default")

    setup_logging(repo_path, console=args.verbose)
    ensure_zircon_dir(repo_path)

    config_path = str(_PROJECT_ROOT / "models.yaml")
    agent = Agent(
        repo_path=repo_path,
        config_path=config_path,
        tier=tier,
        swarm_mode=args.swarm,
        plan_mode=args.plan_mode,
    )

    _print_startup_screen(repo_path, tier)

    # Single event loop for everything
    asyncio.run(main_async(agent))


if __name__ == "__main__":
    main()