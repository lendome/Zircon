"""
Chat component — the main reactive REPL.

Uses the reactive system, theming, keymap, and UI primitives. Reads from
the SDKProvider (transport), ThemeProvider, KeymapProvider, etc. via the
context registry.

Rich is used for rendering. The component reads user input via a
character-by-character event loop (RawTerminal + read_key), dispatches
through the InputBindings (Emacs-style editing), shows autocomplete
suggestions (@file, /slash), the command palette (Ctrl+P), prompt
history, shell mode, stash, external editor, and a status footer.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from typing import Any

from rich.align import Align
from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel as RichPanel
from rich.syntax import Syntax
from rich.text import Text

from ..context import ContextRegistry
from ..reactive.signal import Signal, signal
from ..reactive.lifecycle import LifecycleScope
from ..theming.theme import Theme
from ..keymap.keymap import Keymap
from ..keymap.input_bindings import InputBindings, dispatch_input_action
from ..keymap.definitions import BASE_MODE
from ..keymap.which_key import WhichKeyPanel
from ..prompt.prompt import Prompt, PromptMode
from ..prompt.history import PromptHistory
from ..prompt.stash import PromptStash
from ..prompt.shell_mode import ShellMode
from ..prompt.footer import PromptFooter, FooterData
from ..prompt.editor import open_external_editor
from ..autocomplete.autocomplete import Autocomplete, AutocompleteOption
from ..autocomplete.triggers import TriggerType
from ..autocomplete.file_search import AsyncFileSearch
from ..palette.palette import CommandPalette
from ..palette.registry import CommandRegistry
from ..dialogs.toast import ToastManager, ToastVariant
from ..input.key_reader import (
    RawTerminal,
    _read_key_batch,
    disable_mouse_tracking,
    enable_mouse_tracking,
    is_printable,
    read_key,
)
from ..checkpoint import CheckpointManager, CheckpointPicker


def Panel(*args: Any, **kwargs: Any) -> RichPanel:
    """Create a square-cornered panel for every chat renderer call."""
    kwargs.setdefault("box", box.SQUARE)
    return RichPanel(*args, **kwargs)

_ASCII_ART = [
    "  ______  ___  ____   ____  ___   _   _ ",
    " |__  / |_ _||  _ \\ / ___|/ _ \\ | \\ | |",
    "   / /   | | | |_) | |   | | | ||  \\| |",
    "  / /_   | | |  _ <| |___| |_| || |\\  |",
    " /____| |___||_| \\_\\\\____|\\___/ |_| \\_|",
]


def _strip_thinking(text: str) -> tuple[str, list[str]]:
    thoughts: list[str] = []
    cleaned = re.sub(
        r'<(?:thinking|think)(?:\s[^>]*)?>(.*?)</(?:thinking|think)>',
        lambda m: thoughts.append(m.group(1).strip()) or "",
        text,
        flags=re.DOTALL,
    )
    return cleaned.strip(), thoughts


def _ellide(s: str, max_len: int = 300) -> str:
    return s if len(s) <= max_len else s[:max_len] + "…"


def _truncate_lines(text: str, max_lines: int = 8) -> str:
    """Keep tool output compact while making omitted diff content explicit."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"


def _format_tool_args(name: str, args: dict | None, max_len: int = 120) -> str:
    if not args:
        return name
    preview = str(args)
    if len(preview) > max_len:
        preview = preview[:max_len] + "…"
    return f"{name}({preview})"


def _parse_diff_stats(text: str) -> dict[str, int] | None:
    """Extract added/removed/changed line counts from a unified diff string.

    Returns None if the text doesn't look like a diff.
    """
    if "--- a/" not in text and "+++ b/" not in text and "diff --git" not in text:
        return None
    added = removed = 0
    for line in text.split("\n"):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@") or line.startswith("diff "):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    if added == 0 and removed == 0:
        return None
    return {"added": added, "removed": removed, "changed": added + removed}


_EDIT_TOOL_NAMES = frozenset({"edit_file", "edit_lines", "aider_edit", "create_file", "delete_file"})


def _tool_path(args: dict | None) -> str:
    """Extract the file path from common tool argument shapes."""
    if not args:
        return ""
    return str(args.get("path") or args.get("file") or args.get("file_path") or "")


class TextBuffer:
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


class _ModelPicker:
    """Three-stage keyboard picker: role -> profile -> model.

    Shows ALL profiles regardless of role so the user can assign any
    provider's model to any role.  At the catalog stage the user may
    either pick from a fetched model list or type an arbitrary model
    ID free-text (e.g. any OpenRouter model).
    """

    def __init__(
        self,
        roles: list[str],
        profiles: list[dict[str, Any]],
        theme: Theme,
        initial_role: str = "",
    ) -> None:
        self.roles = roles
        self.profiles = profiles
        self.theme = theme
        self.stage = "role"
        self.index = 0
        self.selected_role = initial_role if initial_role in roles else roles[0]
        self.selected_profile_id = ""
        self._text_buf = ""
        self._fetching = False

    @property
    def options(self) -> list[Any]:
        if self.stage == "role":
            return self.roles
        if self.stage == "profile":
            return self.profiles
        profile = next((item for item in self.profiles if item.get("id") == self.selected_profile_id), None)
        models = list(profile.get("available_models", [])) if profile else []
        current = profile.get("model", "") if profile else ""
        if current and current not in models:
            models.insert(0, current)
        return models

    @property
    def selected_profile(self) -> dict[str, Any] | None:
        if self.stage in ("catalog", "profile"):
            return next((item for item in self.profiles if item.get("id") == self.selected_profile_id), None)
        options = self.options
        return options[self.index] if options else None

    @property
    def selected_model(self) -> str:
        if self.stage == "catalog":
            if self._text_buf:
                return self._text_buf
            opts = self.options
            return str(opts[self.index]) if opts else ""
        profile = self.selected_profile
        return str(profile.get("model", "")) if profile else ""

    def move(self, direction: int) -> None:
        if self.stage == "catalog":
            self._text_buf = ""
        if self.options:
            self.index = (self.index + direction) % len(self.options)

    def type_char(self, ch: str) -> None:
        if self.stage == "catalog":
            self._text_buf += ch

    def backspace_text(self) -> bool:
        if self.stage == "catalog" and self._text_buf:
            self._text_buf = self._text_buf[:-1]
            return True
        return False

    def select_role(self) -> None:
        self.selected_role = str(self.options[self.index])
        self.stage = "profile"
        self.index = 0

    def open_role(self, role: str) -> None:
        """Skip role selection when `/models <role>` was requested."""
        if role not in self.roles:
            return
        self.selected_role = role
        self.stage = "profile"
        self.index = 0

    def select_profile(self) -> None:
        profile = self.selected_profile
        if profile is None:
            return
        self.selected_profile_id = str(profile["id"])
        self.stage = "catalog"
        self.index = 0
        self._text_buf = ""
        if not profile.get("available_models"):
            self._fetching = True

    def provider_label(self) -> str:
        profile = next((item for item in self.profiles if item.get("id") == self.selected_profile_id), None)
        if profile is None:
            return ""
        url = profile.get("base_url", "")
        if "openrouter" in url:
            return "openrouter"
        if "anthropic" in url:
            return "anthropic"
        if "openai" in url:
            return "openai"
        if "ollama" in url or "localhost:11434" in url:
            return "ollama"
        host = url.split("//")[-1].split("/")[0] if url else ""
        return host or "custom"

    def render(self) -> Panel:
        if self.stage == "role":
            title = "Models: choose role"
        elif self.stage == "profile":
            title = f"Models: {self.selected_role}"
        else:
            provider = self.provider_label()
            title = f"Models: {self.selected_role} @ {provider}"

        if self.stage == "role":
            subtitle = "Enter select | Esc cancel"
        elif self.stage == "profile":
            subtitle = "Enter profile | Backspace roles | Esc cancel"
        else:
            subtitle = "Enter assign (or type custom ID) | Backspace profiles | Esc cancel"

        lines = [Text(subtitle, style=f"dim {self.theme.text_muted.to_rich()}")]

        if self.stage == "catalog" and self._fetching:
            lines.append(Text("  Fetching models from provider...", style=f"italic {self.theme.info.to_rich()}"))
            return Panel(Group(*lines), title=title, border_style=self.theme.border_active.to_rich())

        for index, option in enumerate(self.options):
            selected = index == self.index
            marker = "> " if selected else "  "
            style = f"bold {self.theme.primary.to_rich()}" if selected else ""
            if self.stage == "role":
                label = str(option)
            elif self.stage == "profile":
                profile = option
                label = f"{profile.get('id', 'profile')}: {profile.get('model', '')}"
            else:
                label = str(option)
            lines.append(Text(marker + label, style=style))

        if self.stage == "catalog":
            if self._text_buf:
                lines.append(Text(
                    f"  > {self._text_buf}\u2588",
                    style=f"bold {self.theme.warning.to_rich()}",
                ))
            else:
                lines.append(Text(
                    "  [type a custom model ID...]",
                    style=f"dim {self.theme.text_muted.to_rich()}",
                ))

        return Panel(Group(*lines), title=title, border_style=self.theme.border_active.to_rich())


class _SessionPicker:
    """Keyboard-only persisted-session selector."""

    PAGE_SIZE = 12

    def __init__(self, sessions: list[Any], theme: Theme) -> None:
        self.sessions = sorted(
            sessions,
            key=lambda item: (item.is_active, item.updated_at),
            reverse=True,
        )
        self.theme = theme
        self.index = 0

    @property
    def selected(self) -> Any:
        return self.sessions[self.index]

    def move(self, direction: int) -> None:
        self.index = (self.index + direction) % len(self.sessions)

    def move_page(self, direction: int) -> None:
        self.index = max(
            0,
            min(len(self.sessions) - 1, self.index + direction * self.PAGE_SIZE),
        )

    def move_home(self) -> None:
        self.index = 0

    def move_end(self) -> None:
        self.index = len(self.sessions) - 1

    def render(self) -> Panel:
        total = len(self.sessions)
        start = max(0, min(self.index - self.PAGE_SIZE // 2, total - self.PAGE_SIZE))
        visible = self.sessions[start:start + self.PAGE_SIZE]
        lines = [Text(
            f"Up/Down navigate | PgUp/PgDn page | Home/End | Enter resume | Esc back | {self.index + 1}/{total}",
            style=f"dim {self.theme.text_muted.to_rich()}",
        )]
        for index, session in enumerate(visible, start=start):
            selected = index == self.index
            marker = "> " if selected else "  "
            style = f"bold {self.theme.primary.to_rich()}" if selected else ""
            active = "*" if session.is_active else " "
            status = session.status.replace("_", " ")
            files = f" | {session.files_modified} files" if session.files_modified else ""
            lines.append(Text(
                f"{marker}{active} {session.title}  [{status}{files}]  {session.id}",
                style=style,
            ))
        return Panel(Group(*lines), title="Sessions", border_style=self.theme.border_active.to_rich())


class _ReasoningPicker:
    """Keyboard-only reasoning effort selector."""

    EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none"]

    def __init__(self, current: str, theme: Theme) -> None:
        self.current = current
        self.theme = theme
        try:
            self.index = self.EFFORTS.index(current)
        except ValueError:
            self.index = self.EFFORTS.index("medium")

    def move(self, direction: int) -> None:
        self.index = (self.index + direction) % len(self.EFFORTS)

    @property
    def selected(self) -> str:
        return self.EFFORTS[self.index]

    def render(self) -> Panel:
        lines = [Text("Enter select | Esc cancel", style=f"dim {self.theme.text_muted.to_rich()}")]
        for i, effort in enumerate(self.EFFORTS):
            selected = i == self.index
            marker = "> " if selected else "  "
            style = f"bold {self.theme.primary.to_rich()}" if selected else ""
            current_mark = "  (current)" if effort == self.current and not selected else ""
            lines.append(Text(f"{marker}{effort}{current_mark}", style=style))
        return Panel(Group(*lines), title="Reasoning Effort", border_style=self.theme.border_active.to_rich())


class ChatComponent:
    """
    The main chat REPL component with full feature integration.

    Wired systems:
      - Prompt: structured input with parts, extmarks, modes
      - InputBindings: Emacs-style text editing (Ctrl-A/E/K/B/F/D, etc.)
      - Keymap: key dispatch, leader key, mode stack
      - Autocomplete: @file search + /slash commands with live suggestions
      - CommandPalette: Ctrl+P fuzzy command search
      - PromptHistory: Up/Down arrow history navigation
      - PromptStash: save/restore drafts
      - ShellMode: ! prefix for shell commands
      - PromptFooter: model/provider/status display
      - ToastManager: transient notifications
      - WhichKeyPanel: keybinding discovery
      - External editor: $EDITOR integration
    """

    def __init__(self, registry: ContextRegistry) -> None:
        self.registry = registry
        self.scope = LifecycleScope(name="ChatComponent")
        self.console = Console()
        self._transport = registry.get("sdk")
        self._theme_signal: Signal[Theme] = registry.get("theme")
        self._keymap: Keymap = registry.get("keymap")
        self._input: InputBindings = registry.get("input_bindings")
        self._which_key: WhichKeyPanel = registry.get("which_key")
        self._route = registry.get("route")
        self._toast_mgr: ToastManager = registry.get("toast_manager")
        self._data = registry.get("data")
        self._dimensions = registry.get("dimensions")

        # Full prompt system
        self._prompt: Prompt = registry.get("prompt")
        self._history: PromptHistory = registry.get("prompt_history")
        self._stash: PromptStash = registry.get("prompt_stash")
        self._shell = ShellMode()
        self._footer: PromptFooter = registry.get("prompt_footer")
        self._palette: CommandPalette = registry.get("palette")
        self._cmd_registry: CommandRegistry = registry.get("command_registry")
        self._autocomplete: Autocomplete = registry.get("autocomplete")
        self._model_picker: _ModelPicker | None = None
        self._session_picker: _SessionPicker | None = None
        self._reasoning_picker: _ReasoningPicker | None = None
        self._sessions: list[Any] = []
        self._active_session: Any | None = None
        self._restored_message_count = 0
        self._mouse_selection_anchor: int | None = None
        # True while applying intermediate keys of a held-key burst; _render
        # is skipped so a long Ctrl+Arrow hold repaints once, not per tick.
        self._defer_render = False
        self._prompt_origin_row: int | None = None
        self._prompt_render_width = 80
        self._mouse_tracking_enabled = True

        # Checkpoint system — Zircon-owned snapshot reversibility
        self._checkpoint_mgr = CheckpointManager(self._transport)
        self._checkpoint_picker: CheckpointPicker | None = None
        self._last_escape_time: float = 0.0
        self._double_escape_threshold = 0.4  # seconds
        self._last_ctrl_c_time: float = 0.0
        self._double_ctrl_c_threshold = 2.0  # seconds
        self._streaming_task: asyncio.Task | None = None
        self._streaming_cancelled = False

        # Destructive-command approval. While a turn is streaming, the daemon
        # (or in-process registry gate) can ask the user to approve a git-revert
        # / db-mutation command. The run() key loop services the response even
        # though the chat stream is blocked awaiting it.
        self._pending_approval: dict[str, Any] | None = None
        self._coordinator: Any = None
        self._wire_approval()

        # The chat component owns slash command execution, so autocomplete
        # must use this same catalog rather than the incomplete keymap list.
        self._autocomplete.set_slash_commands(self._slash_commands())

        # Wire autocomplete select handler
        self._autocomplete.set_select_handler(self._on_autocomplete_select)

        # Reactive state
        self._status_signal = signal("idle")
        self._is_streaming = signal(False)
        self._spinner_active = signal(False)
        self._running = False
        self._render_lines = 0

        # Collapsed-paste store: placeholder token -> full pasted text.
        # Large pastes live in the prompt as "[Pasted #N: X lines]" tokens so
        # the user can keep editing without scrolling through the payload;
        # tokens are expanded back to the full text at submit time.
        self._pastes: dict[str, str] = {}
        self._paste_counter = 0

    def cleanup(self) -> None:
        self.scope.cleanup()

    # ── Destructive-command approval ─────────────────────────────────────
    def _wire_approval(self) -> None:
        """Connect the registry gate to a user prompt, by transport kind.

        - local (in-process): bind the shared ApprovalCoordinator (set on the
          agent by the default handler) so its Future is resolved here.
        - remote (daemon): register the callback that receives mid-stream
          approval_request frames pushed by the daemon.
        """
        transport = self._transport
        info = getattr(transport, "info", None)
        kind = getattr(info, "kind", "")
        if kind == "local":
            agent = getattr(transport, "_agent", None)
            coordinator = getattr(agent, "approval_coordinator", None)
            if coordinator is not None:
                coordinator.bind(self)
                self._coordinator = coordinator
        elif kind == "remote":
            setter = getattr(transport, "set_approval_callback", None)
            if setter is not None:
                setter(self._on_remote_approval_request)

    def _on_remote_approval_request(self, data: dict[str, Any]) -> None:
        """Receive a daemon-pushed approval request (called from the read loop)."""
        self._set_pending_approval({
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "arguments": data.get("arguments", {}) or {},
            "reason": data.get("reason", ""),
        })

    def _stop_active_live(self) -> None:
        """Stop whatever Live display is currently active so we can print."""
        live = getattr(self.console, "_live", None)
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass
            try:
                self.console._live = None
            except Exception:
                pass

    def _set_pending_approval(self, pending: dict[str, Any]) -> None:
        """Show the approve/deny prompt for a destructive tool call."""
        self._pending_approval = pending
        theme = self._theme_signal.get()
        self._stop_active_live()
        name = pending.get("name", "")
        args = pending.get("arguments", {}) or {}
        reason = pending.get("reason", "")
        preview = _format_tool_args(name, args, 200)
        body = Text()
        body.append("[!] Approve destructive command?\n\n", style=f"bold {theme.warning.to_rich()}")
        body.append(f"{preview}\n\n", style=theme.text.to_rich())
        body.append(reason, style=f"dim {theme.text_muted.to_rich()}")
        self.console.print(Panel(body, title="Approval Required", border_style=theme.warning.to_rich()))
        self.console.print(Text(
            "  Press [y] approve   [n] deny",
            style=f"italic {theme.info.to_rich()}",
        ))
        self._footer.update(is_active=False, status_message="Awaiting approval")

    async def _handle_approval_key(self, key: str) -> None:
        pa = self._pending_approval
        if pa is None:
            return
        if key in ("y", "Y", "return"):
            approved = True
        elif key in ("n", "N", "escape", "ctrl+c", "backspace"):
            approved = False
        else:
            return  # ignore other keys until a decision is made
        await self._resolve_approval(approved)

    async def _resolve_approval(self, approved: bool) -> None:
        pa = self._pending_approval
        self._pending_approval = None
        if pa is None:
            return
        theme = self._theme_signal.get()
        if approved:
            self.console.print(Text("  Approved — continuing.", style=f"bold {theme.success.to_rich()}"))
        else:
            self.console.print(Text("  Denied — operation skipped.", style=f"bold {theme.error.to_rich()}"))
        future = pa.get("future")
        if future is not None:
            # In-process: resolve the coordinator's Future directly.
            if not future.done():
                future.set_result(approved)
        else:
            # Daemon: deliver the decision over a side connection.
            req_id = pa.get("id", "")
            try:
                await self._transport.respond_approval(req_id, approved)
            except Exception as exc:
                self._toast_mgr.error(f"Could not deliver approval response: {exc}")
        self._footer.update(is_active=True, status_message="Resuming...")

    def _restore_terminal(self) -> None:
        """Restore terminal to cooked mode on exit (Windows + Unix)."""
        # Always re-enable the visible caret (the RawTerminal hides it).
        try:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                STD_INPUT_HANDLE = -10
                handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                kernel32.SetConsoleMode(handle, ctypes.c_ulong(0x0007))
            except Exception:
                pass
        else:
            # Unix: stty sane
            try:
                os.system("stty sane")
            except Exception:
                pass

    async def run(self) -> None:
        with self.scope:
            await self._refresh_workspace_state()
            self._update_footer()
            self._print_startup()
            self._render()

            self._running = True
            loop = asyncio.get_event_loop()
            try:
                with RawTerminal():
                    while self._running:
                        try:
                            # Run the blocking key reader in a thread
                            # executor so the event loop stays free to
                            # process background streaming tasks. This is
                            # what lets Escape interrupt an active turn.
                            # _read_key_batch coalesces bursts from a held
                            # repeatable key (e.g. Ctrl+Arrow) into one
                            # delivered event so press-and-hold navigation
                            # repaints at most once per batch.
                            keys = await loop.run_in_executor(None, _read_key_batch)
                            for i, key in enumerate(keys):
                                # Skip intermediate repaints inside a
                                # held-key burst; the final key repaints.
                                self._defer_render = i < len(keys) - 1
                                await self._handle_key(key)
                                if not self._running:
                                    break
                            self._defer_render = False
                        except (EOFError, KeyboardInterrupt):
                            break
                        except Exception as e:
                            import traceback
                            self._restore_terminal()
                            print(f"\nError: {e}")
                            traceback.print_exc()
                            self._running = False
            except (KeyboardInterrupt, asyncio.CancelledError):
                # Ctrl+C arrived as a process signal rather than a readable
                # key (only possible if something re-enabled PROCESSED_INPUT
                # on the console). asyncio.run delivers SIGINT by cancelling
                # the main task, so catch both shapes and exit cleanly
                # instead of dumping a traceback over the UI.
                pass
            finally:
                self._restore_terminal()
            self.console.print()

    async def _handle_key(self, key: str) -> None:
        """Route a key press to the appropriate handler."""

        if key.startswith("mouse:"):
            self._handle_mouse(key)
            return
        # Native terminal selection remains the default. Application mouse
        # tracking is enabled only by the explicit prompt-mouse toggle below.

        # A pending destructive-command approval is serviced even while a turn
        # is streaming (the stream is blocked awaiting this decision).
        if self._pending_approval is not None:
            await self._handle_approval_key(key)
            return

        # Ctrl+C — two presses in a row exit the app from anywhere. A single
        # press interrupts a streaming turn (like Escape) or falls through so
        # open overlays can close on it, with a final catch-all below.
        if key == "ctrl+c":
            now = time.time()
            is_double = (now - self._last_ctrl_c_time) < self._double_ctrl_c_threshold
            self._last_ctrl_c_time = now
            if is_double:
                await self._exit_app()
                return
            if self._is_streaming.get():
                self._cancel_streaming()
                if self._streaming_task is not None:
                    try:
                        await self._streaming_task
                    except asyncio.CancelledError:
                        pass
                self._toast_mgr.info("Turn stopped — press Ctrl+C again to exit")
                return
            # Not streaming: fall through so pickers/palette consume it.

        # While an agent turn is streaming, only Escape and Ctrl+C are honored
        # so the user can interrupt. All other input is dropped until the turn
        # finishes (or is cancelled), at which point typing resumes.
        if self._is_streaming.get() and key not in ("escape", "ctrl+c"):
            return

        # If command palette is open, route there
        if self._palette.is_visible:
            await self._handle_palette_key(key)
            return

        if self._model_picker is not None:
            await self._handle_model_picker_key(key)
            return

        if self._session_picker is not None:
            await self._handle_session_picker_key(key)
            return

        if self._reasoning_picker is not None:
            await self._handle_reasoning_picker_key(key)
            return

        # If checkpoint picker is visible, route there
        if self._checkpoint_picker is not None:
            await self._handle_checkpoint_picker_key(key)
            return

        # If autocomplete is visible, route there
        if self._autocomplete.is_visible:
            if await self._handle_autocomplete_key(key):
                return

        # If which-key panel is visible, route there
        if self._which_key.is_visible:
            self._handle_which_key(key)
            return

        # Bracketed paste — inserted as text, never as keystrokes, so a
        # multiline paste can't submit the prompt. Large pastes collapse to
        # a placeholder token (see _insert_paste).
        if key.startswith("paste:"):
            self._insert_paste(key[len("paste:"):])
            self._render()
            return

        # Ctrl+P opens command palette
        if key == "ctrl+p":
            self._palette.show()
            self._render()
            return

        if key == "ctrl+shift+m":
            self._mouse_tracking_enabled = not getattr(self, "_mouse_tracking_enabled", False)
            if self._mouse_tracking_enabled:
                enable_mouse_tracking()
                self._toast_mgr.info(
                    "Prompt mouse selection enabled; Ctrl+Shift+M restores copy mode"
                )
            else:
                disable_mouse_tracking()
                self._toast_mgr.info(
                    "Native terminal selection enabled; drag to copy agent output"
                )
            self._render()
            return

        # Session switching is a first-class action in the active TUI. Keep a
        # direct binding because the declarative leader keymap is not yet wired.
        if key == "ctrl+l":
            await self._show_session_picker(self._theme_signal.get())
            return

        # Keep model selection available without entering a slash command.
        if key in self._keymap.get_key_sequences("model_list"):
            await self._show_model_picker(self._theme_signal.get())
            return

        # Leader key / app-level commands
        if self._keymap.dispatch_key(key):
            return

        # Input editing actions
        if self._try_input_action(key):
            self._render()
            return

        # History navigation (Up/Down)
        if key == "up" and self._input.cursor == 0:
            entry = self._history.previous(self._input.text)
            if entry is not None:
                self._input.set_text(entry.input, cursor=len(entry.input))
            self._autocomplete.hide()
            self._render()
            return
        if key == "down" and self._input.cursor == len(self._input.text):
            entry = self._history.next()
            if entry is not None:
                self._input.set_text(entry.input, cursor=len(entry.input))
            self._autocomplete.hide()
            self._render()
            return

        # Submit (return)
        if key == "return":
            await self._submit()
            return

        # Newline (shift+return etc.)
        if key in ("shift+return", "ctrl+return", "alt+return", "ctrl+j"):
            self._input.insert("\n")
            self._render()
            return

        # Printable character
        if is_printable(key):
            self._input.insert(key)
            # Check for autocomplete trigger
            await self._check_autocomplete()
            self._render()
            return

        # Escape — stop streaming, clear input, or double-escape for checkpoints
        if key == "escape":
            now = time.time()
            is_double = (now - self._last_escape_time) < self._double_escape_threshold
            self._last_escape_time = now

            # If streaming, single escape stops the current turn
            if self._is_streaming.get():
                self._cancel_streaming()
                # Wait for the streaming task to finish cleaning up its
                # Live displays before rendering anything new.
                if self._streaming_task is not None:
                    try:
                        await self._streaming_task
                    except asyncio.CancelledError:
                        pass
                if is_double:
                    await self._show_checkpoint_picker()
                else:
                    self._toast_mgr.info("Turn stopped. You can type now.")
                return

            # Not streaming: double-escape shows checkpoint picker
            if is_double:
                self._last_escape_time = 0.0
                await self._show_checkpoint_picker()
                return

            # Single escape (not streaming): clear input or exit shell mode
            if self._shell.active:
                self._shell.exit()
                self._prompt.mode = PromptMode.NORMAL
            else:
                self._input.set_text("")
                self._clear_pastes()
            self._autocomplete.hide()
            self._render()
            return

        # Ctrl+C that no overlay consumed: clear the prompt and hint that a
        # second press exits.
        if key == "ctrl+c":
            if self._input.text:
                self._input.set_text("")
                self._clear_pastes()
                self._autocomplete.hide()
            self._toast_mgr.info("Press Ctrl+C twice to exit")
            self._render()
            return

    async def _exit_app(self) -> None:
        """Exit the TUI, cancelling any in-flight turn first."""
        if self._is_streaming.get():
            self._cancel_streaming()
            if self._streaming_task is not None:
                try:
                    await self._streaming_task
                except asyncio.CancelledError:
                    pass
        self._toast_mgr.info("Goodbye!")
        self._running = False

    # ── Collapsed pastes ───────────────────────────────────────────────

    @property
    def _paste_store(self) -> dict[str, str]:
        """Lazily initialized so tests that build the component with
        __new__ (bypassing __init__) still render safely."""
        store = getattr(self, "_pastes", None)
        if store is None:
            store = {}
            self._pastes = store
        return store

    def _insert_paste(self, payload: str) -> None:
        """Insert a bracketed-paste payload at the cursor.

        Small pastes go in verbatim. Large ones (3+ lines or 150+ chars,
        matching prompt/paste.py's smart_paste thresholds) are stored and
        shown as a "[Pasted #N: X lines]" token so the payload never floods
        the editing line; the token is expanded on submit.
        """
        text = payload.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        line_count = len(text.splitlines())
        if line_count < 3 and len(text) < 150:
            self._input.insert(text)
        else:
            self._paste_counter = getattr(self, "_paste_counter", 0) + 1
            if line_count > 1:
                token = f"[Pasted #{self._paste_counter}: {line_count} lines]"
            else:
                token = f"[Pasted #{self._paste_counter}: {len(text)} chars]"
            self._paste_store[token] = text
            self._input.insert(token)
        self._autocomplete.hide()

    def _expand_pastes(self, text: str) -> str:
        """Replace placeholder tokens with their full pasted content."""
        for token, content in sorted(self._paste_store.items(), key=lambda kv: -len(kv[0])):
            if token in text:
                text = text.replace(token, content)
        return text

    def _token_spans(self) -> list[tuple[int, int]]:
        """(start, end) offsets of every live paste token in the input."""
        text = self._input.text
        spans: list[tuple[int, int]] = []
        for token in self._paste_store:
            idx = text.find(token)
            while idx >= 0:
                spans.append((idx, idx + len(token)))
                idx = text.find(token, idx + len(token))
        return spans

    def _token_ending_at(self, pos: int) -> tuple[int, int] | None:
        for start, end in self._token_spans():
            if end == pos:
                return (start, end)
        return None

    def _token_starting_at(self, pos: int) -> tuple[int, int] | None:
        for start, end in self._token_spans():
            if start == pos:
                return (start, end)
        return None

    def _snap_cursor(self, direction: int) -> None:
        """Keep the cursor out of the interior of a paste token — tokens are
        atomic, so arrows jump over them. direction <0 snaps left, >0 right."""
        cursor = self._input.cursor
        for start, end in self._token_spans():
            if direction > 0 and start <= cursor < end:
                self._input.set_cursor(end)
                return
            if direction < 0 and start < cursor < end:
                self._input.set_cursor(start)
                return

    def _clear_pastes(self) -> None:
        self._paste_store.clear()

    def _try_input_action(self, key: str) -> bool:
        """Try to match key against input editing bindings."""
        from ..keymap.definitions import Definitions

        # Check each input binding definition
        for name, defn in Definitions.items():
            if not name.startswith("input_"):
                continue
            if name == "input_submit":
                continue
            key_seqs = self._keymap.get_key_sequences(name)
            if key in key_seqs:
                if name == "input_delete_char_backward":
                    # Paste tokens are atomic: backspace at the end of one
                    # removes the whole placeholder, not one char of it.
                    span = None if self._input.has_selection else self._token_ending_at(self._input.cursor)
                    if span is not None:
                        token = self._input.delete(span[0], span[1])
                        self._paste_store.pop(token, None)
                    else:
                        self._input.delete_char_backward()
                    self._autocomplete.hide()
                    return True
                if name == "input_delete_char_forward":
                    span = None if self._input.has_selection else self._token_starting_at(self._input.cursor)
                    if span is not None:
                        token = self._input.delete(span[0], span[1])
                        self._paste_store.pop(token, None)
                    else:
                        self._input.delete_char_forward()
                    return True
                if name == "input_delete_word_backward":
                    self._input.delete_word_backward()
                    self._autocomplete.hide()
                    return True
                if name == "input_delete_word_forward":
                    self._input.delete_word_forward()
                    return True
                if name == "input_delete_to_line_end":
                    self._input.delete_to_line_end()
                    return True
                if name == "input_delete_to_line_start":
                    self._input.delete_to_line_start()
                    return True
                if name == "input_delete_line":
                    self._input.delete_line()
                    return True
                if name == "input_move_left":
                    self._input.move_left()
                    self._snap_cursor(-1)
                    return True
                if name == "input_move_right":
                    self._input.move_right()
                    self._snap_cursor(1)
                    return True
                if name == "input_line_home":
                    self._input.move_home()
                    return True
                if name == "input_line_end":
                    self._input.move_end()
                    return True
                if name == "input_word_forward":
                    self._input.move_word_forward()
                    return True
                if name == "input_word_backward":
                    self._input.move_word_backward()
                    return True
                if name == "input_undo":
                    self._input.undo()
                    return True
                if name == "input_redo":
                    self._input.redo()
                    return True
                if name == "input_select_all":
                    self._input.select_all()
                    return True
                if dispatch_input_action(self._input, name):
                    return True
        return False

    def _render(self) -> None:
        """Render the current input state, footer, and overlays.

        Skipped while draining an intermediate held-key burst — the final
        key of the burst performs the one visible repaint.

        Renders everything to a StringIO buffer first (so we can count
        the exact number of lines), then moves the cursor up by that
        count on the next render, clears, and reprints. This is purely
        screen-relative and works on Windows ConPTY.
        """
        import io
        import shutil
        from rich.console import Console as RichConsole

        if self._defer_render:
            return

        theme = self._theme_signal.get()

        # Keep all theme-dependent components up to date
        self._toast_mgr.theme = theme
        self._footer.theme = theme
        self._which_key.theme = theme

        # Render to a buffer to count lines. Ask the tty directly for its
        # width — shutil.get_terminal_size() trusts a possibly stale COLUMNS
        # env var, and rendering wider than the real terminal wraps lines,
        # which desyncs the row count and orphans stale lines on repaint.
        try:
            width = os.get_terminal_size(sys.stdout.fileno()).columns
        except Exception:
            width = shutil.get_terminal_size((80, 24)).columns
        self._prompt_render_width = width
        buf = io.StringIO()
        render_console = RichConsole(
            file=buf, width=width, force_terminal=True, color_system="auto",
            legacy_windows=False,
        )

        # Build the prompt line
        text = self._input.text
        cursor = self._input.cursor

        prefix = "! " if self._shell.active else "> "
        prefix_style = theme.warning.to_rich() if self._shell.active else theme.primary.to_rich()

        prompt_line = Text()
        prompt_line.append(prefix, style=f"bold {prefix_style}")
        self._append_input_text(prompt_line, text, cursor, theme)
        render_console.print(prompt_line)

        # Render autocomplete if visible
        if self._autocomplete.is_visible:
            state = self._autocomplete.state
            if state.options:
                sel_style = theme.primary.to_rich()
                dim_style = theme.text_muted.to_rich()
                bg_style = theme.background_element.to_rich()
                trigger_char = "@" if state.trigger.value == "@" else "/"
                render_console.print(Text(f"  {trigger_char} autocomplete", style=f"dim {dim_style}"))
                for i, opt in enumerate(state.options[:8]):
                    is_sel = i == state.selected
                    marker = "> " if is_sel else "  "
                    if is_sel:
                        line = Text()
                        line.append(marker, style=f"bold {sel_style}")
                        line.append(opt.display, style=f"bold {sel_style} on {bg_style}")
                        line.append(f"  {opt.category}", style=f"dim {dim_style} on {bg_style}")
                    else:
                        line = Text()
                        line.append(marker, style=dim_style)
                        line.append(opt.display)
                        line.append(f"  {opt.category}", style=f"dim {dim_style}")
                    render_console.print(line)

        # Render footer
        render_console.print(self._footer.render())

        # Render toast if visible
        if self._toast_mgr.is_visible:
            render_console.print(self._toast_mgr.render())

        # Render which-key if visible
        if self._which_key.is_visible:
            render_console.print(self._which_key.render())

        if self._model_picker is not None:
            render_console.print(self._model_picker.render())

        if self._session_picker is not None:
            render_console.print(self._session_picker.render())

        if self._reasoning_picker is not None:
            render_console.print(self._reasoning_picker.render())

        if self._checkpoint_picker is not None:
            render_console.print(self._checkpoint_picker.render())

        if self._palette.is_visible:
            render_console.print(self._palette.render())

        # Emit the frame as ONE write: move to the top of the previous
        # region, erase, and repaint — wrapped in a synchronized-output
        # block (DEC 2026) so supporting terminals commit it atomically.
        # Clearing and repainting in separate writes flashes a blank
        # region on every keypress.
        output = buf.getvalue()
        frame: list[str] = ["\x1b[?2026h"]
        if self._render_lines > 0:
            frame.append(f"\x1b[{self._render_lines}A")
        frame.append("\x1b[J")
        frame.append(output)
        frame.append("\x1b[?2026l")
        self._render_lines = self._count_rows(output, width)
        sys.stdout.write("".join(frame))
        sys.stdout.flush()

    def _handle_mouse(self, event: str) -> None:
        """Translate an SGR click/drag over the prompt into text selection."""
        if not getattr(self, "_mouse_tracking_enabled", False):
            return
        if (
            getattr(getattr(self, "_palette", None), "is_visible", False)
            or getattr(self, "_model_picker", None) is not None
            or getattr(self, "_session_picker", None) is not None
            or getattr(self, "_reasoning_picker", None) is not None
            or getattr(self, "_checkpoint_picker", None) is not None
        ):
            return
        try:
            _, action, button, column, row = event.split(":", 4)
            button_i = int(button)
            column_i = int(column)
            row_i = int(row)
        except (TypeError, ValueError):
            return
        if action in {"wheel_up", "wheel_down"}:
            # Mouse reporting steals wheel gestures from terminal scrollback.
            # Release it immediately; the remaining ticks in this gesture then
            # scroll natively. Any later keyboard input re-enables prompt drag.
            if getattr(self, "_mouse_tracking_enabled", True):
                disable_mouse_tracking()
                self._mouse_tracking_enabled = False
            return
        if button_i != 0 or self._is_streaming.get():
            return

        if self._prompt_origin_row is None:
            cursor_row = self._get_cursor_row()
            self._prompt_origin_row = max(1, cursor_row - self._render_lines)
        offset = self._prompt_offset_at(column_i, row_i)
        if offset is None:
            if action == "up":
                self._mouse_selection_anchor = None
            return

        if action == "down":
            self._mouse_selection_anchor = offset
            self._input.set_cursor(offset)
        elif action == "drag" and self._mouse_selection_anchor is not None:
            self._input.set_selection(self._mouse_selection_anchor, offset)
        elif action == "up":
            if self._mouse_selection_anchor is not None:
                self._input.set_selection(self._mouse_selection_anchor, offset)
            self._mouse_selection_anchor = None
        else:
            return
        self._autocomplete.hide()
        self._render()


    def _prompt_offset_at(self, column: int, row: int) -> int | None:
        """Map a 1-based terminal cell to the nearest prompt text offset."""
        from rich.cells import cell_len

        origin = self._prompt_origin_row
        if origin is None:
            return None
        target_row = row - origin
        if target_row < 0:
            return None
        width = max(1, int(self._prompt_render_width))
        text = self._input.text
        cursor = self._input.cursor
        x = 2  # "> " / "! " prefix
        y = 0
        positions: list[tuple[int, int, int]] = []

        def advance(cells: int) -> None:
            nonlocal x, y
            for _ in range(max(0, cells)):
                x += 1
                if x >= width:
                    x = 0
                    y += 1

        for offset in range(len(text) + 1):
            positions.append((offset, x, y))
            if offset == len(text):
                break
            if offset == cursor:
                advance(1)  # software cursor block inserted before the char
            char = text[offset]
            if char == "\n":
                x = 0
                y += 1
            else:
                advance(max(1, cell_len(char)))

        row_positions = [item for item in positions if item[2] == target_row]
        if not row_positions:
            return None
        target_column = max(0, column - 1)
        return min(row_positions, key=lambda item: abs(item[1] - target_column))[0]

    def _append_input_text(self, line: Text, text: str, cursor: int, theme: Theme) -> None:
        """Append the input text to the prompt line with the cursor block at
        the right offset, paste-token placeholders highlighted, and any active
        selection shown reversed."""
        cursor_style = theme.primary.to_rich()
        token_style = f"bold {theme.info.to_rich()}"
        selection_style = "reverse"

        spans = sorted(self._token_spans())
        sel_start: int | None = None
        sel_end: int | None = None
        # getattr so test doubles (SimpleNamespace input) render without the
        # full InputBindings API
        if getattr(self._input, "has_selection", False):
            sel = self._input.get_selection_bounds()
            if sel is not None:
                sel_start, sel_end = sel

        # Build styled pieces: (segment_text, style). Paste tokens get the
        # token style; selected ranges get reversed video on top.
        pieces: list[tuple[int, int, str | None]] = []
        pos = 0
        for start, end in spans:
            if start > pos:
                pieces.append((pos, start, None))
            pieces.append((start, end, token_style))
            pos = end
        if pos < len(text):
            pieces.append((pos, len(text), None))

        block = "\u2588"
        emitted_cursor = False

        def emit(seg_text: str, style: str | None, seg_start: int) -> None:
            nonlocal emitted_cursor
            if not seg_text:
                return
            # Split the segment at the selection boundaries, if any
            cuts: list[tuple[str, str | None]] = []
            if sel_start is not None and sel_end is not None:
                i = 0
                while i < len(seg_text):
                    g = seg_start + i
                    in_sel = sel_start <= g < sel_end
                    j = i
                    while j < len(seg_text) and (sel_start <= seg_start + j < sel_end) == in_sel:
                        j += 1
                    if in_sel:
                        chunk_style = f"{style} {selection_style}" if style else selection_style
                    else:
                        chunk_style = style
                    cuts.append((seg_text[i:j], chunk_style))
                    i = j
            else:
                cuts.append((seg_text, style))
            for chunk, chunk_style in cuts:
                line.append(chunk, style=chunk_style)

        for start, end, style in pieces:
            seg_text = text[start:end]
            if not emitted_cursor and start <= cursor < end:
                emit(text[start:cursor], style, start)
                line.append(block, style=cursor_style)
                emit(text[cursor:end], style, cursor)
                emitted_cursor = True
            else:
                emit(seg_text, style, start)
        if not emitted_cursor:
            line.append(block, style=cursor_style)

    @staticmethod
    def _count_rows(output: str, width: int) -> int:
        """Physical rows the output occupies, accounting for line wraps.

        A line wider than the terminal occupies multiple rows; counting
        only "\\n" would undercount, and the next repaint would move up
        too few rows, leaving stale lines above the prompt region.
        """
        import re
        from rich.cells import cell_len

        ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
        rows = 0
        for line in output.split("\n")[:-1]:
            visible = cell_len(ansi.sub("", line))
            rows += 1 if visible <= width else -(-visible // width)
        return rows

    def _clear_render(self) -> None:
        """Clear the currently rendered prompt region before printing output."""
        if self._render_lines > 0:
            sys.stdout.write(f"\033[{self._render_lines}A")
            sys.stdout.write("\033[J")
            sys.stdout.flush()
        self._render_lines = 0

    @staticmethod
    def _get_cursor_row() -> int:
        """Get the current cursor row via ANSI cursor position report.

        On Windows 10+ (ConPTY) and modern terminals this works.
        Falls back to row 1 if it fails.
        """
        if sys.platform == "win32":
            return ChatComponent._get_cursor_row_windows()
        return ChatComponent._get_cursor_row_unix()

    @staticmethod
    def _get_cursor_row_windows() -> int:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            class COORD(ctypes.Structure):
                _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
            class CSBI(ctypes.Structure):
                _fields_ = [
                    ("dwSize", COORD),
                    ("dwCursorPosition", COORD),
                    ("wAttributes", ctypes.c_ushort),
                    ("srWindow", ctypes.c_short * 4),
                    ("dwMaximumWindowSize", COORD),
                ]
            csbi = CSBI()
            kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(csbi))
            return csbi.dwCursorPosition.Y + 1
        except Exception:
            return 1

    @staticmethod
    def _get_cursor_row_unix() -> int:
        try:
            import select

            sys.stdout.write("\033[6n")
            sys.stdout.flush()
            resp = b""
            fd = sys.stdin.fileno()
            deadline = time.monotonic() + 0.08
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if not select.select([fd], [], [], max(0.0, remaining))[0]:
                    break
                ch = os.read(fd, 1)
                if not ch:
                    break
                resp += ch
                if ch == b"R":
                    break
            text = resp.decode("ascii", errors="replace")
            match = re.search(r"\[(\d+);(\d+)R", text)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 1

    def _render_autocomplete(self, theme: Theme) -> None:
        """Render the autocomplete dropdown as simple lines (no panel border)."""
        state = self._autocomplete.state
        if not state.options:
            return

        trigger_char = "@" if state.trigger.value == "file" else "/"
        sel_style = theme.primary.to_rich()
        dim_style = theme.text_muted.to_rich()
        bg_style = theme.background_element.to_rich()

        # Header line
        self.console.print(Text(f"  {trigger_char} autocomplete", style=f"dim {dim_style}"))

        # Options — one per line, max 8
        for i, opt in enumerate(state.options[:8]):
            is_sel = i == state.selected
            marker = "> " if is_sel else "  "
            if is_sel:
                line = Text()
                line.append(marker, style=f"bold {sel_style}")
                line.append(opt.display, style=f"bold {sel_style} on {bg_style}")
                line.append(f"  {opt.category}", style=f"dim {dim_style} on {bg_style}")
            else:
                line = Text()
                line.append(marker, style=dim_style)
                line.append(opt.display)
                line.append(f"  {opt.category}", style=f"dim {dim_style}")
            self.console.print(line)

    async def _check_autocomplete(self) -> None:
        """Check if autocomplete should trigger after text change."""
        text = self._input.text
        cursor = self._input.cursor

        # Check shell mode entry
        if self._shell.should_enter(text, cursor) and not self._shell.active:
            if text.startswith("!"):
                self._shell.enter()
                self._prompt.mode = PromptMode.SHELL
                self._toast_mgr.info("Shell mode — command will be executed, not sent to LLM", duration=2.0)

        # Check autocomplete trigger
        visible = self._autocomplete.detect(text, cursor)
        if visible:
            await self._autocomplete.filter(text, os.getcwd())
        elif not self._autocomplete.is_visible:
            # Check for prompt.detect_autocomplete_trigger as well
            trigger = self._prompt.detect_autocomplete_trigger()
            if trigger and not self._autocomplete.is_visible:
                self._autocomplete.detect(text, cursor)
                if self._autocomplete.is_visible:
                    await self._autocomplete.filter(text, os.getcwd())

    async def _handle_autocomplete_key(self, key: str) -> bool:
        """Handle keys while autocomplete is visible. Returns True if consumed."""
        if key == "return" or key == "tab":
            self._autocomplete.select()
            self._render()
            return True
        if key == "escape":
            self._autocomplete.hide()
            self._render()
            return True
        if key in ("up", "ctrl+p"):
            self._autocomplete.move_up()
            self._render()
            return True
        if key in ("down", "ctrl+n"):
            self._autocomplete.move_down()
            self._render()
            return True
        # If it's a printable char or backspace, fall through to normal editing
        if is_printable(key) or key == "backspace":
            self._autocomplete.hide()
            return False
        # Anything else — let it fall through
        self._autocomplete.hide()
        return False

    def _on_autocomplete_select(self, opt: AutocompleteOption) -> None:
        """Called when user selects an autocomplete option."""
        state = self._autocomplete.state
        text = self._input.text

        if state.trigger == TriggerType.FILE:
            # Replace @query with @filepath
            replace = opt.value
            # Check for line range
            data = opt.data or {}
            line_range = data.get("line_range")
            if line_range and hasattr(line_range, "has_range") and line_range.has_range:
                replace = f"{replace}{line_range.label}"

            cursor = self._input.cursor
            before = text[:state.index]
            after = text[cursor:]
            self._input.set_text(
                before + "@" + replace + " " + after,
                cursor=len(before) + len(replace) + 2,
            )
            # Insert as a prompt part
            self._prompt.insert_file_mention(opt.value)
        elif state.trigger == TriggerType.SLASH:
            # Replace /query with /command
            cursor = self._input.cursor
            before = text[:state.index]
            after = text[cursor:]
            self._input.set_text(
                before + opt.value + " " + after,
                cursor=len(before) + len(opt.value) + 1,
            )
            if opt.on_select is not None:
                opt.on_select()

    def _slash_commands(self) -> list[dict[str, Any]]:
        """Return every command executable by this TUI instance."""
        commands = [
            ("help", "Show help and keybindings"),
            ("keys", "Show keybindings"),
            ("status", "Show workspace status"),
            ("tier", "Show or change execution tier"),
            ("fast", "Toggle fast mode (fastest providers)"),
            ("models", "Change role models: /models <role> [model-id]"),
            ("sessions", "List and resume saved sessions"),
            ("resume", "Resume the most recent session"),
            ("compact", "Compact conversation history"),
            ("plugins", "Show configured plugins"),
            ("reset", "Clear conversation context"),
            ("approve", "Approve the pending plan"),
            ("task", "Run a full coding task"),
            ("theme", "List or change theme"),
            ("stash", "Save the current prompt"),
            ("stash-pop", "Restore the latest stashed prompt"),
            ("stash-list", "List stashed prompts"),
            ("editor", "Edit prompt in external editor"),
            ("notifications", "Toggle notifications"),
            ("reasoning", "Show or change reasoning effort"),
            ("nitro", "Toggle OpenRouter Nitro mode"),
            ("exit", "Exit Zircon"),
        ]
        return [
            {
                "display": f"/{name}",
                "description": description,
            }
            for name, description in commands
        ]

    async def _handle_palette_key(self, key: str) -> None:
        """Handle keys while the command palette is open."""
        if key == "escape" or key == "ctrl+c":
            self._palette.hide()
            self._render()
            return
        if key == "return":
            self._palette.select()
            self._render()
            return
        if key in ("up", "ctrl+p"):
            self._palette.move_up()
            self._render()
            return
        if key in ("down", "ctrl+n"):
            self._palette.move_down()
            self._render()
            return
        if key == "backspace":
            new_filter = self._palette.filter[:-1]
            self._palette.set_filter(new_filter)
            self._render()
            return
        if key.startswith("paste:"):
            pasted = key[len("paste:"):]
            flattened = " ".join(pasted.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
            self._palette.set_filter(self._palette.filter + flattened.strip())
            self._render()
            return
        if is_printable(key):
            self._palette.set_filter(self._palette.filter + key)
            self._render()
            return

    def _handle_which_key(self, key: str) -> None:
        """Handle keys while which-key panel is visible."""
        if key == "escape" or key == "return" or key == "ctrl+c":
            self._which_key.hide()
            self._render()
            return
        if key == "up":
            self._which_key.scroll_up()
            self._render()
            return
        if key == "down":
            self._which_key.scroll_down()
            self._render()
            return

    async def _submit(self) -> None:
        """Submit the current input."""
        text = self._input.text.strip()
        if not text:
            return

        # Expand collapsed-paste placeholders to their full content
        text = self._expand_pastes(text)
        self._clear_pastes()

        # Shell mode
        if self._shell.active:
            cmd = text.lstrip("!").strip()
            if cmd:
                self._run_shell(cmd)
            self._shell.exit()
            self._prompt.mode = PromptMode.NORMAL
            self._input.set_text("")
            self._render()
            return

        # Add to history
        self._history.add(text, timestamp=time.time())
        self._history.reset()

        # Remove the prompt and footer before committing this input to history.
        self._input.set_text("")
        self._autocomplete.hide()
        self._clear_render()

        # Slash commands
        if text.startswith("/"):
            theme = self._theme_signal.get()
            self.console.print(Text(f"> {text}", style=f"bold {theme.primary.to_rich()}"))
            should_exit = await self._handle_command(text)
            if should_exit:
                self._running = False
            return

        # Regular chat — print "You:" immediately so the screen isn't blank
        # while the pre-turn checkpoint is being created, then stream.
        theme = self._theme_signal.get()
        self.console.print(Text(f"  You: {text}", style=theme.text.to_rich()))
        # Create a git checkpoint before the agent turn for reversibility
        try:
            cp = await self._checkpoint_mgr.create_checkpoint(label=text[:60])
            if cp is not None:
                self._toast_mgr.info(f"Checkpoint: {cp.sha}", duration=1.0)
        except Exception:
            pass
        buf = TextBuffer()
        self._streaming_task = asyncio.create_task(self._stream_chat_wrapped(text, buf))

    def _print_startup(self) -> None:
        theme = self._theme_signal.get()
        project = self.registry.get("project")
        dims = self._dimensions.get()

        self.console.clear()
        self.console.print(Align.center(Text("\n".join(_ASCII_ART), style=theme.primary.to_rich())))
        self.console.print(Align.center(Text("Autonomous Coding Agent", style=theme.info.to_rich())))
        self.console.print()
        self.console.print(Align.center(Text(f"Workspace: {project.workspace}", style=theme.text_muted.to_rich())))
        self.console.print(Align.center(Text(f"Transport: {self._transport.info.kind}", style=theme.text_muted.to_rich())))
        self.console.print(Align.center(Text(f"Terminal: {dims.width}x{dims.height}", style=theme.text_muted.to_rich())))
        self.console.print()
        self.console.print(self._workspace_summary(theme))
        self.console.print()
        self.console.print(Align.center(Text(
            "Type to send a prompt | Ctrl+L sessions | / commands | @ files | Ctrl+P palette",
            style=theme.text_muted.to_rich(),
        )))
        self.console.print(Align.center(Text(
            "Esc stops the agent | Esc Esc reverts to a checkpoint | Ctrl+Shift+M prompt mouse mode",
            style=f"dim {theme.text_muted.to_rich()}",
        )))
        self.console.print(Align.center(Text(
            "Ctrl+A/E: line start/end | Ctrl+\u2190/\u2192: word jump | Shift+arrows: select | Ctrl+W: del word | Ctrl+Z/Y: undo/redo",
            style=f"dim {theme.text_muted.to_rich()}",
        )))
        self.console.print()

    async def _refresh_workspace_state(self) -> None:
        """Load status and persisted sessions before rendering the workspace."""
        try:
            await self._data.refresh(self._transport)
        except Exception:
            pass
        try:
            lifecycle = self.registry.get("session_lifecycle")
            self._sessions = await lifecycle.refresh_sessions()
        except Exception:
            self._sessions = []
        self._active_session = next(
            (session for session in self._sessions if session.is_active),
            None,
        )

    def _workspace_summary(self, theme: Theme) -> Panel:
        """Render current and recent sessions on the actual startup screen."""
        lines: list[RenderableType] = []
        if self._active_session is not None:
            active = self._active_session
            lines.append(Text(
                f"ACTIVE  {active.title}  {active.id}",
                style=f"bold {theme.primary.to_rich()}",
            ))
            lines.append(Text(
                f"        {active.status.replace('_', ' ')} | {active.files_modified} modified file(s)",
                style=theme.text_muted.to_rich(),
            ))
        else:
            lines.append(Text("NEW SESSION  Start typing to begin", style=f"bold {theme.primary.to_rich()}"))

        recent = [session for session in self._sessions if not session.is_active][:3]
        if recent:
            lines.append(Text(""))
            lines.append(Text("RECENT", style=f"bold {theme.info.to_rich()}"))
            for session in recent:
                lines.append(Text(
                    f"  {session.title[:54]:54} {session.status.replace('_', ' '):14} {session.id}",
                    style=theme.text_muted.to_rich(),
                ))
        lines.append(Text(""))
        lines.append(Text(
            f"{len(self._sessions)} saved session(s) | Ctrl+L switch | /resume latest",
            style=f"dim {theme.text_muted.to_rich()}",
        ))
        return Panel(
            Group(*lines),
            title="Session Workspace",
            border_style=theme.border_active.to_rich(),
            padding=(0, 1),
        )

    def _update_footer(self) -> None:
        """Update the footer with current model/provider info."""
        theme = self._theme_signal.get()
        active_session = getattr(self, "_active_session", None)
        model_id = ""
        provider = ""

        try:
            status = self._data.status
            model_id = str(status.get("model", ""))
            provider = str(status.get("provider", ""))
        except Exception:
            pass
        if not model_id:
            try:
                info = self._transport.info
                model_id = getattr(info, "model", "") or str(getattr(info, "model_id", ""))
                provider = getattr(info, "provider", "")
            except Exception:
                pass

        self._footer.update(
            agent_name="Zircon",
            model_id=model_id or "default",
            provider=provider or "local",
            session_title=getattr(active_session, "title", ""),
            session_id=getattr(active_session, "id", ""),
            context_used_tokens=int(status.get("context_used_tokens", 0)) if "status" in locals() else 0,
            context_max_tokens=int(status.get("context_max_tokens", 0)) if "status" in locals() else 0,
            cost=float(status.get("session_cost_usd", 0.0)) if "status" in locals() else 0.0,
            status_message="Ready",
            is_active=False,
            show_interrupt=False,
        )
        self._footer.theme = theme

    async def _handle_command(self, user_input: str) -> bool:
        theme = self._theme_signal.get()
        parts = user_input.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/exit":
            self._toast_mgr.info("Goodbye!")
            return True

        if cmd == "/help":
            self._print_help(theme)
            return False

        if cmd == "/help" or cmd == "/keys" or cmd == "/which-key":
            self._which_key.toggle()
            self._render_lines = 0; self._render()
            return False

        if cmd == "/status":
            await self._data.refresh(self._transport)
            s = self._data.status
            self.console.print(Panel(
                f"Status:    {s.get('status', '?')}\n"
                f"Tier:      {s.get('tier', '?')}\n"
                f"Working:   {s.get('working_set', '?')} files\n"
                f"Modified:  {s.get('modified_files', '?')} files\n"
                f"History:   {s.get('history', '?')} messages\n"
                f"Context:   {int(s.get('context_used_tokens', 0)):,} / "
                f"{int(s.get('context_max_tokens', 0)):,} tokens "
                f"({float(s.get('context_percent', 0.0)):.1f}%)\n"
                f"Cost:      ${float(s.get('session_cost_usd', 0.0)):.4f}",
                title="Status",
                border_style=theme.border_active.to_rich(),
            ))
            return False

        if cmd == "/tier":
            return await self._handle_tier_command(arg, theme)

        if cmd == "/reasoning":
            return await self._handle_reasoning_command(arg, theme)

        if cmd == "/nitro":
            return await self._handle_nitro_command(arg)

        if cmd == "/fast":
            return await self._handle_fast_command(arg)

        if cmd == "/models":
            return await self._handle_models_command(arg, theme)

        if cmd == "/resume":
            return await self._resume_last_session(theme)

        if cmd in {"/sessions", "/continue"}:
            return await self._show_session_picker(theme)

        if cmd in {"/compact", "/summarize"}:
            try:
                result = await self._transport.compact_context()
                if result.get("ok"):
                    self._toast_mgr.success("Conversation compacted")
                else:
                    self._toast_mgr.warning(result.get("error", "Could not compact conversation"))
            except Exception as exc:
                self._toast_mgr.error(f"Could not compact conversation: {exc}")
            self._render_lines = 0; self._render()
            return False

        if cmd == "/plugins":
            return self._show_plugins(theme)

        if cmd == "/reset":
            await self._transport.reset_context()
            self._toast_mgr.success("Context cleared")
            self._render_lines = 0; self._render()
            return False

        if cmd == "/approve":
            await self._transport.submit_feedback("approved")
            self._toast_mgr.info("Plan approved, continuing...")
            self._render_lines = 0; self._render()
            buf = TextBuffer()
            self._streaming_task = asyncio.create_task(self._stream_chat_wrapped("", buf))
            return False

        if cmd == "/task":
            if not arg:
                self._toast_mgr.warning("Usage: /task <description>")
                self._render_lines = 0; self._render()
                return False
            self.console.print(Text(f"Task: {arg}", style=theme.warning.to_rich()))
            # Create a git checkpoint before the task for reversibility
            try:
                cp = await self._checkpoint_mgr.create_checkpoint(label=f"task: {arg[:50]}")
                if cp is not None:
                    self._toast_mgr.info(f"Checkpoint: {cp.sha}", duration=1.0)
            except Exception:
                pass
            buf = TextBuffer()
            self._streaming_task = asyncio.create_task(self._stream_task_wrapped(arg, buf))
            return False

        if cmd == "/theme":
            return self._handle_theme_command(arg, theme)

        if cmd == "/stash":
            if not self._input.text.strip():
                self._toast_mgr.warning("Nothing to stash")
                self._render_lines = 0; self._render()
                return False
            self._stash.push(self._expand_pastes(self._input.text))
            self._input.set_text("")
            self._clear_pastes()
            self._toast_mgr.success(f"Prompt stashed ({self._stash.count} total)")
            self._render_lines = 0; self._render()
            return False

        if cmd == "/stash-pop":
            entry = self._stash.pop()
            if entry is None:
                self._toast_mgr.warning("No stashed prompts")
                self._render_lines = 0; self._render()
                return False
            self._input.set_text(entry.input)
            self._toast_mgr.info("Prompt restored from stash")
            self._render_lines = 0; self._render()
            return False

        if cmd == "/stash-list":
            entries = self._stash.list()
            if not entries:
                self._toast_mgr.info("No stashed prompts")
            else:
                lines = [f"  {i+1}. {e.input[:60]}{'...' if len(e.input)>60 else ''}" for i, e in enumerate(entries)]
                self.console.print(Panel(Text("\n".join(lines)), title="Stashed Prompts", border_style=theme.border_active.to_rich()))
            return False

        if cmd == "/editor":
            # Expand paste placeholders first — the external editor must see
            # the real content, not "[Pasted #N: X lines]" tokens.
            edited = open_external_editor(self._expand_pastes(self._input.text), renderer=None, cwd=os.getcwd())
            if edited is not None:
                self._input.set_text(edited)
                self._clear_pastes()
                self._toast_mgr.success("Edited in external editor")
            else:
                self._toast_mgr.warning("No $EDITOR set")
            self._render_lines = 0; self._render()
            return False

        if cmd == "/notifications":
            try:
                attention = self.registry.get("attention")
                if attention is not None:
                    attention.toggle_notifications()
                    enabled = attention.notifications_enabled
                    self._toast_mgr.info(f"Notifications {'on' if enabled else 'off'}")
            except Exception:
                self._toast_mgr.warning("Attention manager not available")
            self._render_lines = 0; self._render()
            return False

        command = next(
            (item for item in self._cmd_registry.get_slash_commands()
             if cmd.lstrip("/") in {item.slash_name, *item.slash_aliases}),
            None,
        )
        if command is not None:
            self._toast_mgr.info(f"{command.title} is available through its slash command")
            return False

        self._toast_mgr.warning(f"Unknown command: {cmd}. Type /help.")
        self._render_lines = 0; self._render()
        return False

    async def _handle_reasoning_command(self, arg: str, theme: Theme) -> bool:
        if arg:
            VALID_EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none"]
            effort = arg.strip().lower()
            if effort not in VALID_EFFORTS:
                self._toast_mgr.warning(
                    f"Invalid effort: {effort}. Choices: {', '.join(VALID_EFFORTS)}"
                )
                self._render_lines = 0
                self._render()
                return False
            try:
                result = await self._transport.set_reasoning_effort(effort)
            except Exception as exc:
                self._toast_mgr.error(f"Could not set reasoning effort: {exc}")
                self._render_lines = 0
                self._render()
                return False
            if not result.get("ok"):
                self._toast_mgr.warning(result.get("error", "Could not set reasoning effort"))
            else:
                self._toast_mgr.success(f"Reasoning effort: {result.get('effort', effort)}")
            self._render_lines = 0
            self._render()
            return False

        current = "medium"
        try:
            await self._data.refresh(self._transport)
            current = self._data.status.get("reasoning_effort", "medium")
        except Exception:
            pass
        self._reasoning_picker = _ReasoningPicker(current, theme)
        self._render()
        return False

    async def _handle_nitro_command(self, arg: str) -> bool:
        value = arg.strip().lower()
        if value not in {"on", "off"}:
            self._toast_mgr.warning("Usage: /nitro <on|off>")
            self._render_lines = 0
            self._render()
            return False
        enabled = value == "on"
        try:
            result = await self._transport.set_nitro_mode(enabled)
        except Exception as exc:
            self._toast_mgr.error(f"Could not set Nitro mode: {exc}")
        else:
            if result.get("ok"):
                self._toast_mgr.success(f"Nitro mode: {'on' if result.get('nitro_mode') else 'off'}")
            else:
                self._toast_mgr.warning(result.get("error", "Could not set Nitro mode"))
        self._render_lines = 0
        self._render()
        return False

    def _handle_theme_command(self, arg: str, theme: Theme) -> bool:
        from ..theming.themes import list_themes

        if not arg:
            themes = list_themes(theme.mode)
            lines = [f"  {name}" for name in sorted(themes.keys())]
            self.console.print(Panel(Text("\n".join(lines)), title="Available Themes", border_style=theme.border_active.to_rich()))
            return False

        theme_name_signal = self.registry.get("theme_name")
        theme_name_signal.set(arg)
        self._toast_mgr.success(f"Theme: {arg}")
        self._render()
        return False

    async def _handle_fast_command(self, arg: str) -> bool:
        """Toggle fast mode (highest-throughput provider routing)."""
        state = arg.strip().lower()
        try:
            status = await self._transport.get_status()
            current = bool(status.get("fast_mode", False))
        except Exception:
            current = False

        if not state:
            self._toast_mgr.info(f"Fast mode is {'on' if current else 'off'}")
            self._render_lines = 0
            self._render()
            return False

        if state in ("on", "true", "enable"):
            target = True
        elif state in ("off", "false", "disable"):
            target = False
        elif state == "toggle":
            target = not current
        else:
            self._toast_mgr.warning("Usage: /fast <on|off|toggle>")
            self._render_lines = 0
            self._render()
            return False

        try:
            result = await self._transport.set_fast_mode(target)
        except Exception as exc:
            self._toast_mgr.error(f"Could not set fast mode: {exc}")
            self._render_lines = 0
            self._render()
            return False

        if result.get("ok"):
            self._toast_mgr.success(f"Fast mode {'on' if target else 'off'}")
        else:
            self._toast_mgr.warning(result.get("error", "Could not set fast mode"))
        self._render_lines = 0
        self._render()
        return False

    async def _handle_tier_command(self, arg: str, theme: Theme) -> bool:
        from ...daemon.transport import resolve_tier_name

        # No argument: display the current tier.
        if not arg:
            try:
                await self._data.refresh(self._transport)
                current = self._data.status.get("tier", "?")
            except Exception:
                current = "?"
            self.console.print(Panel(
                f"Current tier: {current}\n"
                f"Usage: /tier <fast|balanced|quality>",
                title="Tier",
                border_style=theme.border_active.to_rich(),
            ))
            self._render_lines = 0
            self._render()
            return False

        resolved = resolve_tier_name(arg)
        if resolved is None:
            self._toast_mgr.warning(
                f"Unknown tier: {arg}. Choices: fast, balanced, quality"
            )
            self._render_lines = 0
            self._render()
            return False

        try:
            result = await self._transport.set_tier(arg)
        except Exception as exc:
            self._toast_mgr.error(f"Could not switch tier: {exc}")
            self._render_lines = 0
            self._render()
            return False

        if not result.get("ok"):
            self._toast_mgr.warning(
                result.get("error", "Could not switch tier")
            )
        else:
            await self._data.refresh(self._transport)
            self._update_footer()
            context_window = int(result.get("context_window", 0))
            context_label = f" | context {context_window // 1000}K" if context_window else ""
            self._toast_mgr.success(
                f"Tier: {result.get('tier', resolved)}{context_label}"
            )
        self._render_lines = 0
        self._render()
        return False

    def _print_help(self, theme: Theme) -> None:
        from ..keymap.definitions import Definitions

        lines: list[str] = []
        lines.append("[bold]Slash Commands[/]")
        lines.append("  /help          This help")
        lines.append("  /status        Show workspace status")
        lines.append("  /tier [name]   Show or switch tier (fast|balanced|quality)")
        lines.append("  /fast [on|off] Toggle fast mode (highest-throughput routing)")
        lines.append("  /models [scan]             Choose a role, then assign a profile model")
        lines.append("  /models <role> [model-id]  Change one role; omit model ID to open its picker")
        lines.append("  /sessions      Resume a persisted session")
        lines.append("  /resume        Resume the most recent session")
        lines.append("  /compact       Summarize history to free context")
        lines.append("  /plugins       Show configured plugin status")
        lines.append("  /reset         Clear context")
        lines.append("  /approve       Approve a pending plan")
        lines.append("  /task <desc>   Run a full agent task")
        lines.append("  /theme <name>  Switch theme")
        lines.append("  /stash         Stash current prompt")
        lines.append("  /stash-pop     Restore stashed prompt")
        lines.append("  /stash-list    List stashed prompts")
        lines.append("  /editor        Edit in $EDITOR")
        lines.append("  /notifications Toggle notifications")
        lines.append("  /reasoning     Show or change reasoning effort (max|xhigh|high|medium|low|minimal|none)")
        lines.append("  /nitro on|off  Toggle OpenRouter Nitro mode")
        lines.append("  /keys          Show all keybindings")
        lines.append("  /exit          Quit")
        lines.append("")
        lines.append("[bold]Key Bindings[/]")
        lines.append("  Ctrl+P         Command palette")
        lines.append("  Ctrl+A / Ctrl+E  Line start / end")
        lines.append("  Ctrl+B / Ctrl+F  Move left / right")
        lines.append("  Alt+B / Alt+F    Word backward / forward")
        lines.append("  Ctrl+W          Delete word backward")
        lines.append("  Ctrl+K          Delete to end of line")
        lines.append("  Ctrl+U          Delete to start of line")
        lines.append("  Ctrl+Z / Ctrl+Y  Undo / redo")
        lines.append("  Up / Down       History navigation")
        lines.append("  @mention        File autocomplete")
        lines.append("  /slash          Command autocomplete")
        lines.append("  !command        Shell mode")
        lines.append("  Return          Submit prompt")
        lines.append("  Escape          Stop turn / clear input / exit mode")
        lines.append("  Esc Esc         Revert to a git checkpoint")

        self.console.print(Panel(
            "\n".join(lines),
            title="Help",
            border_style=theme.border_active.to_rich(),
        ))

    async def _handle_models_command(self, arg: str, theme: Theme) -> bool:
        """Handle `/models [scan|role [model-id]]` and persist role updates."""
        parts = arg.split(maxsplit=1)
        refresh = bool(parts and parts[0].lower() in {"refresh", "scan"})
        role = "" if refresh or not parts else parts[0]
        model_id = "" if len(parts) < 2 else parts[1].strip()

        try:
            data = await self._transport.list_models(refresh=refresh)
        except Exception as exc:
            self._toast_mgr.error(f"Could not load models: {exc}")
            self._render()
            return False

        roles = list(data.get("roles", []))
        if role and role not in roles:
            self._toast_mgr.warning(f"Unknown role: {role}. Available: {', '.join(roles)}")
            self._render()
            return False

        if model_id:
            priority = data.get("role_priority", {}).get(role, [])
            profile_id = str(priority[0]) if priority else ""
            if not profile_id:
                profile_id = next((str(profile.get("id")) for profile in data.get("profiles", []) if role in profile.get("roles", [])), "")
            if not profile_id:
                self._toast_mgr.warning(f"No profile is configured for role: {role}")
                self._render()
                return False
            try:
                result = await self._transport.set_model(role, profile_id, model_id)
            except Exception as exc:
                self._toast_mgr.error(f"Could not save model: {exc}")
            else:
                if result.get("ok"):
                    self._toast_mgr.success(f"{role}: {result['model']}")
                    await self._data.refresh(self._transport)
                    self._update_footer()
                else:
                    self._toast_mgr.warning(result.get("error", "Could not save model"))
            self._render()
            return False

        return await self._show_model_picker(theme, refresh=refresh, initial_role=role, data=data)

    async def _show_model_picker(
        self,
        theme: Theme,
        refresh: bool = False,
        initial_role: str = "",
        data: dict[str, Any] | None = None,
    ) -> bool:
        try:
            if data is None:
                data = await self._transport.list_models(refresh=refresh)
        except Exception as exc:
            self._toast_mgr.error(f"Could not load models: {exc}")
            self._render()
            return False
        profiles = list(data.get("profiles", []))
        roles = list(data.get("roles", []))
        if not profiles or not roles:
            self._toast_mgr.warning("No model profiles or roles are configured")
            self._render()
            return False
        catalog = data.get("catalog", {})
        for profile in profiles:
            discovered = catalog.get(profile.get("id"), [])
            if discovered:
                profile["available_models"] = discovered
        self._model_picker = _ModelPicker(roles=roles, profiles=profiles, theme=theme, initial_role=initial_role)
        if initial_role:
            self._model_picker.open_role(initial_role)
        self._render()
        return False

    async def _handle_model_picker_key(self, key: str) -> None:
        picker = self._model_picker
        if picker is None:
            return
        if key in {"escape", "ctrl+c"}:
            self._model_picker = None
        elif key in {"up", "ctrl+p"}:
            picker.move(-1)
        elif key in {"down", "ctrl+n"}:
            picker.move(1)
        elif key in {"backspace", "left"}:
            if picker.stage == "catalog":
                if picker.backspace_text():
                    pass
                else:
                    picker.stage = "profile"
                    picker.index = 0
            elif picker.stage == "profile":
                picker.stage = "role"
                picker.index = 0
        elif key in {"return", "tab", "right"}:
            if picker.stage == "role":
                picker.select_role()
            elif picker.stage == "profile":
                picker.select_profile()
                if picker._fetching:
                    self._render()
                    data = await self._transport.list_models(refresh=True)
                    catalog = data.get("catalog", {})
                    for profile in picker.profiles:
                        discovered = catalog.get(profile.get("id"), [])
                        if discovered:
                            profile["available_models"] = discovered
                    picker._fetching = False
                self._render()
                return
            else:
                model_id = picker.selected_model
                if not model_id:
                    self._toast_mgr.warning("Enter a model ID first")
                    self._render()
                    return
                selected = picker.selected_profile
                if selected is not None:
                    try:
                        result = await self._transport.set_model(
                            picker.selected_role,
                            str(selected["id"]),
                            model_id,
                        )
                    except Exception as exc:
                        self._toast_mgr.error(f"Could not save model: {exc}")
                    else:
                        if result.get("ok"):
                            self._toast_mgr.success(f"{picker.selected_role}: {result['model']}")
                            self._model_picker = None
                            await self._data.refresh(self._transport)
                            self._update_footer()
                        else:
                            self._toast_mgr.warning(result.get("error", "Could not save model"))
        elif is_printable(key) and picker.stage == "catalog":
            picker.type_char(key)
        self._render()

    async def _handle_reasoning_picker_key(self, key: str) -> None:
        picker = self._reasoning_picker
        if picker is None:
            return
        if key in {"escape", "ctrl+c"}:
            self._reasoning_picker = None
        elif key in {"up", "ctrl+p"}:
            picker.move(-1)
        elif key in {"down", "ctrl+n"}:
            picker.move(1)
        elif key in {"return", "tab"}:
            effort = picker.selected
            self._reasoning_picker = None
            try:
                result = await self._transport.set_reasoning_effort(effort)
            except Exception as exc:
                self._toast_mgr.error(f"Could not set reasoning effort: {exc}")
            else:
                if result.get("ok"):
                    self._toast_mgr.success(f"Reasoning effort: {result.get('effort', effort)}")
                    await self._data.refresh(self._transport)
                    self._update_footer()
                else:
                    self._toast_mgr.warning(result.get("error", "Could not set reasoning effort"))
        self._render()

    async def _resume_last_session(self, theme: Theme) -> bool:
        """``/resume`` — immediately resume the most recent session (no picker)."""
        try:
            lifecycle = self.registry.get("session_lifecycle")
            sessions = await lifecycle.refresh_sessions()
        except Exception as exc:
            self._toast_mgr.error(f"Could not load sessions: {exc}")
            self._render()
            return False
        if not sessions:
            self._toast_mgr.info("No persisted sessions to resume")
            self._render()
            return False
        target = await lifecycle.continue_last()
        if target is None:
            self._toast_mgr.warning("No resumable session found")
            self._render()
            return False
        await self._resume_and_render(target.id)
        return False

    async def _show_session_picker(self, theme: Theme) -> bool:
        try:
            lifecycle = self.registry.get("session_lifecycle")
            sessions = await lifecycle.refresh_sessions()
        except Exception as exc:
            self._toast_mgr.error(f"Could not load sessions: {exc}")
            self._render()
            return False
        if not sessions:
            self._toast_mgr.info("No persisted sessions")
            self._render()
            return False
        self._sessions = sessions
        self._active_session = next(
            (session for session in sessions if session.is_active),
            self._active_session,
        )
        self._session_picker = _SessionPicker(sessions, theme)
        self._render()
        return False

    async def _handle_session_picker_key(self, key: str) -> None:
        picker = self._session_picker
        if picker is None:
            return
        if key in {"escape", "ctrl+c"}:
            self._session_picker = None
        elif key in {"up", "ctrl+p"}:
            picker.move(-1)
        elif key in {"down", "ctrl+n"}:
            picker.move(1)
        elif key == "pageup":
            picker.move_page(-1)
        elif key == "pagedown":
            picker.move_page(1)
        elif key == "home":
            picker.move_home()
        elif key == "end":
            picker.move_end()
        elif key in {"r", "ctrl+r"}:
            await self._show_session_picker(self._theme_signal.get())
            return
        elif key in {"return", "tab"}:
            if picker.selected is None:
                self._render()
                return
            session_id = picker.selected.id
            self._session_picker = None
            await self._resume_and_render(session_id)
            return
        self._render()

    def _show_plugins(self, theme: Theme) -> bool:
        try:
            host = self.registry.get("plugin_runtime")
            runtime = host.runtime if host is not None else None
            statuses = runtime.status if runtime is not None else []
        except Exception:
            statuses = []
        if not statuses:
            text = "No plugins configured. Add plugin entries through TUI configuration."
        else:
            text = "\n".join(
                f"  {'enabled' if item.get('enabled') else 'disabled'}  {item.get('name', item.get('id', 'plugin'))}"
                for item in statuses
            )
        self.console.print(Panel(Text(text), title="Plugins", border_style=theme.border_active.to_rich()))
        return False

    def _run_shell(self, command: str) -> None:
        """Execute a shell command and display output."""
        theme = self._theme_signal.get()
        import subprocess

        from ....core.proc_spawn import popen_kwargs

        self.console.print(Text(f"  $ {command}", style=theme.warning.to_rich()))
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
                **popen_kwargs(),
            )
            if result.stdout:
                self.console.print(Text(result.stdout.rstrip()))
            if result.stderr:
                self.console.print(Text(result.stderr.rstrip(), style=theme.error.to_rich()))
            self._toast_mgr.info(f"Exit code: {result.returncode}")
        except Exception as e:
            self._toast_mgr.error(f"Shell error: {e}")

    def _print_markdown(self, text: str, theme: Theme) -> None:
        """Print markdown-formatted text with proper terminal width."""
        import shutil
        from rich.console import Console as RichConsole

        width = shutil.get_terminal_size((80, 24)).columns
        # Use a fresh console with explicit width for correct wrapping
        md_console = RichConsole(width=width, force_terminal=True, color_system="auto")
        md_console.print(Markdown(text, code_theme="ansi_dark"))

    def _render_prior_messages(self, messages: list[dict], *, render_prompt: bool = True) -> None:
        """Replay a persisted session's chat history into the terminal scrollback.

        Accepts either the persisted ``{type, text}`` schema or the OpenAI-style
        ``{role, content}`` schema (defensively handles both). Tool results are
        rendered as compact muted lines.
        """
        theme = self._theme_signal.get()
        self._clear_render()
        rendered = 0
        for msg in messages:
            role = msg.get("role")
            if role is None:
                mtype = msg.get("type", "")
                role = {"user": "user", "text": "assistant", "tool_result": "tool"}.get(mtype)
            content = msg.get("content")
            if content is None:
                content = msg.get("text", "")
            if isinstance(content, list):
                content = "\n".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if not content and role != "assistant":
                continue
            if role == "user":
                self.console.print(Text(f"  You: {content}", style=theme.text.to_rich()))
                rendered += 1
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if content:
                    self.console.print(Text("  Zircon:", style=f"bold {theme.secondary.to_rich()}"))
                if content:
                    self._print_markdown(str(content), theme)
                for call in tool_calls:
                    function = call.get("function", {}) if isinstance(call, dict) else {}
                    name = function.get("name", "tool")
                    arguments = function.get("arguments", "")
                    self.console.print(Text(
                        f"  [tool call] {name} {arguments}",
                        style=theme.info.to_rich(),
                    ))
                if content or tool_calls:
                    rendered += 1
            elif role == "tool":
                text = content.get("content", "") if isinstance(content, dict) else content
                snippet = str(text).strip()
                if snippet:
                    self.console.print(
                        Text(f"  [tool result]\n{snippet}", style=theme.text_muted.to_rich())
                    )
                    rendered += 1
        if rendered:
            self.console.print(Text(f"  — {rendered} message(s) restored —", style=theme.text_muted.to_rich()))
        self._render_lines = 0
        if render_prompt:
            self._render()

    def _print_session_header(self, session: Any, message_count: int) -> None:
        theme = self._theme_signal.get()
        project = self.registry.get("project")
        self.console.print(Panel(
            Group(
                Text(session.title, style=f"bold {theme.primary.to_rich()}"),
                Text(
                    f"{session.id} | {session.status.replace('_', ' ')} | "
                    f"{message_count} messages | {project.workspace}",
                    style=theme.text_muted.to_rich(),
                ),
            ),
            title="Active Session",
            border_style=theme.border_active.to_rich(),
        ))

    async def _resume_and_render(self, session_id: str) -> bool:
        """Resume a session and replay its history into the chat transcript."""
        try:
            lifecycle = self.registry.get("session_lifecycle")
            session = await lifecycle.resume(session_id)
        except Exception as exc:
            self._toast_mgr.error(f"Could not resume session: {exc}")
            self._render()
            return False
        if session is None:
            self._toast_mgr.warning("Session could not be resumed")
            self._render()
            return False
        self._active_session = session
        self._restored_message_count = len(lifecycle.resumed_messages)
        await self._data.refresh(self._transport)
        self._update_footer()
        self._clear_render()
        self.console.clear()
        self._render_lines = 0
        self._prompt_origin_row = None
        self._print_session_header(session, self._restored_message_count)
        self._render_prior_messages(lifecycle.resumed_messages, render_prompt=False)
        self._render()
        self._toast_mgr.success(
            f"Resumed {session.id}: {len(lifecycle.resumed_messages)} messages"
        )
        return True

    def _cancel_streaming(self) -> None:
        """Cancel the current streaming agent turn."""
        self._streaming_cancelled = True
        if self._streaming_task is not None and not self._streaming_task.done():
            self._streaming_task.cancel()
        self._is_streaming.set(False)
        self._footer.update(is_active=False, status_message="Stopped", show_interrupt=False)
        self._clear_render()

    async def _show_checkpoint_picker(self) -> bool:
        """Show the checkpoint picker for reverting to a previous state."""
        theme = self._theme_signal.get()
        try:
            checkpoints = await self._checkpoint_mgr.list_checkpoints(20)
        except Exception as exc:
            self._toast_mgr.error(f"Could not load checkpoints: {exc}")
            self._render()
            return False
        if not checkpoints:
            self._toast_mgr.info("No checkpoints available")
            self._render()
            return False
        self._checkpoint_picker = CheckpointPicker(checkpoints, theme)
        self._render()
        return False

    async def _handle_checkpoint_picker_key(self, key: str) -> None:
        """Handle keys while the checkpoint picker is visible."""
        picker = self._checkpoint_picker
        if picker is None:
            return
        picker.handle_key(key)
        if not picker.is_visible:
            if picker.cancelled:
                self._checkpoint_picker = None
                self._render()
                return
            # User confirmed a checkpoint selection
            selected = picker.selected
            self._checkpoint_picker = None
            if selected is not None:
                self._clear_render()
                theme = self._theme_signal.get()
                self.console.print(Text(
                    f"  Reverting to checkpoint {selected.sha}...",
                    style=theme.warning.to_rich(),
                ))
                try:
                    ok = await self._checkpoint_mgr.revert_checkpoint(selected.sha)
                except Exception as exc:
                    self._toast_mgr.error(f"Revert failed: {exc}")
                    ok = False
                if ok:
                    self._toast_mgr.success(f"Reverted to {selected.sha}")
                    self.console.print(Text(
                        f"  {selected.message}",
                        style=f"dim {theme.text_muted.to_rich()}",
                    ))
                else:
                    self._toast_mgr.error("Could not revert to checkpoint")
            self._render_lines = 0
            self._render()
            return
        self._render()

    async def _stream_chat_wrapped(self, message: str, buf: TextBuffer) -> None:
        """Background-task wrapper around _stream_chat.

        Ensures streaming state is reset and the prompt is re-rendered
        after the turn finishes, is cancelled via Escape, or errors out.
        """
        try:
            await self._stream_chat(message, buf)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._report_stream_error(exc)
        finally:
            self._streaming_task = None
            self._render_lines = 0
            self._render()

    async def _stream_task_wrapped(self, task: str, buf: TextBuffer) -> None:
        """Background-task wrapper around _stream_task."""
        try:
            await self._stream_task(task, buf)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._report_stream_error(exc)
        finally:
            self._streaming_task = None
            self._render_lines = 0
            self._render()

    async def _report_stream_error(self, exc: BaseException) -> None:
        """Surface a streaming error instead of leaving a blank screen.

        _stream_chat/_stream_task run as background tasks; an unhandled
        exception there is swallowed by asyncio and the Live panels are
        never closed, leaving the TUI blank. Tear down any live panels and
        print the error so the user knows why the turn stopped.
        """
        theme = self._theme_signal.get()
        self._clear_render()
        self.console.print(Text(
            f"  [error] {type(exc).__name__}: {exc}",
            style=theme.error.to_rich(),
        ))
        self._toast_mgr.error(f"Turn failed: {exc}")

    async def _stream_chat(self, message: str, buf: TextBuffer) -> None:
        theme = self._theme_signal.get()
        reasoning_acc = ""
        reasoning_live: Live | None = None
        text_live: Live | None = None
        text_acc = ""
        # Tool execution live panel state
        tool_live: Live | None = None
        tool_label = ""
        tool_start_t = 0.0
        tool_refresh_task: asyncio.Task[None] | None = None
        _spinner_frames = "|/-\\"
        _spinner_idx = 0
        # Track the last announced tool call so its result can use the name/args
        pending_tool_name = ""
        pending_tool_args: dict | None = None
        # Activity live panel state (indexing, classification, planning, etc.)
        activity_live: Live | None = None
        activity_label = ""
        activity_start_t = 0.0
        activity_refresh_task: asyncio.Task[None] | None = None
        # Track the model currently producing output for footer/panel display
        current_model = self._footer.data.model_id

        def _safe_stop(live: Live | None) -> None:
            if live is not None:
                try:
                    live.stop()
                except Exception:
                    pass
                # Force-clear the console's live reference so the next
                # Live.start() doesn't raise "Only one live display may be
                # active at once" if stop() itself threw.
                try:
                    self.console._live = None
                except Exception:
                    pass

        def _start_live(live: Live) -> None:
            try:
                live.start()
            except Exception:
                _safe_stop(live)
                live.start()

        def close_reasoning() -> None:
            nonlocal reasoning_live, reasoning_acc
            _safe_stop(reasoning_live)
            reasoning_live = None
            reasoning_acc = ""

        def stop_text_live() -> None:
            """Stop the text Live display without touching text_acc."""
            nonlocal text_live
            _safe_stop(text_live)
            text_live = None

        def flush_text() -> None:
            """Stop the text Live display and permanently print accumulated text.

            The text Live uses transient=True, so stopping it erases the
            on-screen content.  This re-prints the accumulated text so it
            persists in the scrollback.
            """
            nonlocal text_live, text_acc
            stop_text_live()
            if text_acc:
                clean, thoughts = _strip_thinking(text_acc)
                for t in thoughts:
                    self.console.print(Panel(Text(t, style=theme.info.to_rich()), title="Thought", border_style=theme.border.to_rich()))
                if clean:
                    self._print_markdown(clean, theme)
            text_acc = ""

        def close_tool_live() -> None:
            """Stop the tool execution Live panel."""
            nonlocal tool_live, tool_label, tool_refresh_task
            if tool_refresh_task is not None:
                tool_refresh_task.cancel()
                tool_refresh_task = None
            _safe_stop(tool_live)
            tool_live = None
            tool_label = ""

        def _tool_panel_content() -> Group:
            nonlocal _spinner_idx
            _spinner_idx = (_spinner_idx + 1) % len(_spinner_frames)
            spinner = _spinner_frames[_spinner_idx]
            elapsed = time.time() - tool_start_t if tool_start_t else 0.0
            lines = [
                Text(f"  {spinner} {tool_label}", style=f"bold {theme.secondary.to_rich()}"),
                Text(f"    running... {elapsed:.1f}s", style=theme.text_muted.to_rich()),
            ]
            if current_model:
                lines.append(Text(f"    model: {current_model}", style=f"dim {theme.info.to_rich()}"))
            return Group(*lines)

        def start_tool_live(label: str) -> None:
            nonlocal tool_live, tool_label, tool_start_t, tool_refresh_task
            close_tool_live()
            tool_label = label
            tool_start_t = time.time()
            tool_live = Live(
                _tool_panel_content(),
                console=self.console,
                refresh_per_second=10,
                transient=True,
            )
            _start_live(tool_live)
            tool_refresh_task = asyncio.create_task(_refresh_tool_live())

        async def _refresh_tool_live() -> None:
            """Rebuild the dynamic tool panel while a command is running."""
            try:
                while tool_live is not None:
                    await asyncio.sleep(0.1)
                    if tool_live is not None:
                        tool_live.update(_tool_panel_content())
            except asyncio.CancelledError:
                pass

        def update_tool_live(extra: str = "") -> None:
            nonlocal tool_live
            if tool_live is not None:
                if extra:
                    content = _tool_panel_content()
                    tool_live.update(Group(content, Text(f"    {extra}", style=theme.text_muted.to_rich())))
                else:
                    tool_live.update(_tool_panel_content())

        def close_activity_live() -> None:
            """Stop the activity live panel (indexing, planning, etc.)."""
            nonlocal activity_live, activity_label, activity_refresh_task
            if activity_refresh_task is not None:
                activity_refresh_task.cancel()
                activity_refresh_task = None
            _safe_stop(activity_live)
            activity_live = None
            activity_label = ""

        def _activity_panel_content() -> Group:
            nonlocal _spinner_idx
            _spinner_idx = (_spinner_idx + 1) % len(_spinner_frames)
            spinner = _spinner_frames[_spinner_idx]
            elapsed = time.time() - activity_start_t if activity_start_t else 0.0
            lines = [
                Text(f"  {spinner} {activity_label}", style=f"bold {theme.info.to_rich()}"),
                Text(f"    working... {elapsed:.1f}s", style=theme.text_muted.to_rich()),
            ]
            if current_model:
                lines.append(Text(f"    model: {current_model}", style=f"dim {theme.info.to_rich()}"))
            return Group(*lines)

        def start_activity_live(label: str) -> None:
            nonlocal activity_live, activity_label, activity_start_t, activity_refresh_task
            close_activity_live()
            activity_label = label
            activity_start_t = time.time()
            activity_live = Live(
                _activity_panel_content(),
                console=self.console,
                refresh_per_second=10,
                transient=True,
            )
            _start_live(activity_live)
            activity_refresh_task = asyncio.create_task(_refresh_activity_live())

        async def _refresh_activity_live() -> None:
            """Rebuild the dynamic activity panel while work is in progress."""
            try:
                while activity_live is not None:
                    await asyncio.sleep(0.1)
                    if activity_live is not None:
                        activity_live.update(_activity_panel_content())
            except asyncio.CancelledError:
                pass

        def update_activity_live() -> None:
            nonlocal activity_live
            if activity_live is not None:
                activity_live.update(_activity_panel_content())

        def _render_tool_result(name: str, args: dict | None, result_text: str) -> None:
            """Render a completed tool's result with diff stats when available."""
            stats = _parse_diff_stats(result_text)
            path = _tool_path(args)

            if stats:
                # Edit tools — show a compact stats line + the diff
                added = stats["added"]
                removed = stats["removed"]
                parts = [f"+{added}", f"-{removed}"]
                stats_line = "  ".join(parts)
                title_bits = [name]
                if path:
                    title_bits.append(path)
                title_bits.append(stats_line)
                panel_title = " · ".join(title_bits)

                self.console.print(Panel(
                    Syntax(_truncate_lines(result_text), "diff", theme="ansi_dark", word_wrap=True),
                    title=panel_title,
                    border_style=theme.warning.to_rich(),
                ))
            elif name in _EDIT_TOOL_NAMES and path:
                # create_file / delete_file — no diff but still an edit
                self.console.print(Text(
                    f"  {name}({path})  {result_text[:200]}",
                    style=f"bold {theme.secondary.to_rich()}",
                ))
            elif "--- a/" in result_text or "+++ b/" in result_text or "diff --git" in result_text:
                # Fallback diff detection for any tool
                self.console.print(Panel(
                    Syntax(_truncate_lines(result_text), "diff", theme="ansi_dark", word_wrap=True),
                    title="Diff",
                    border_style=theme.warning.to_rich(),
                ))
            else:
                self.console.print(Panel(Text(_ellide(result_text)), title="Tool Result", border_style=theme.text_muted.to_rich()))

        self._is_streaming.set(True)
        self._streaming_cancelled = False
        self._footer.update(is_active=True, status_message="Thinking...", show_interrupt=True)
        start_activity_live("Thinking...")
        try:
            async for chunk in self._transport.chat_stream(message):
                if self._streaming_cancelled:
                    break
                chunk_model = chunk.get("model") or ""
                if chunk_model and chunk_model != current_model:
                    current_model = chunk_model
                    self._footer.update(model_id=current_model)
                    if activity_live is not None:
                        update_activity_live()
                    if tool_live is not None:
                        update_tool_live()
                if chunk.get("reasoning"):
                    close_activity_live()
                    reasoning_acc += chunk["reasoning"]
                    reasoning_style = theme.text.to_rich()
                    rendered = Text(reasoning_acc, style=reasoning_style)
                    max_lines = max(self.console.height - 12, 16)
                    all_lines = reasoning_acc.split("\n")
                    if len(all_lines) > max_lines:
                        tail = "\n".join(all_lines[-max_lines:])
                        indicator = f"... ({len(all_lines) - max_lines} earlier lines)"
                        visible = Text(indicator + "\n" + tail, style=reasoning_style)
                    else:
                        visible = rendered
                    panel = Panel(
                        visible,
                        title="Reasoning",
                        border_style=theme.border.to_rich(),
                    )
                    if reasoning_live is None:
                        reasoning_live = Live(panel, console=self.console, refresh_per_second=12, transient=False)
                        _start_live(reasoning_live)
                    else:
                        reasoning_live.update(panel)
                    continue

                close_reasoning()

                status = chunk.get("status")
                if status == "awaiting_input":
                    close_activity_live()
                    close_tool_live()
                    flush_text()
                    text = chunk.get("text") or "Agent requires your approval."
                    self.console.print(Panel(Text(text, style=theme.info.to_rich()), title="Plan", border_style=theme.border_active.to_rich()))
                    self.console.print(Text("  Type /approve to continue.", style=f"italic {theme.info.to_rich()}"))
                    self._footer.update(is_active=False, status_message="Awaiting approval", show_interrupt=False)
                    return

                if status == "failed" and chunk.get("done"):
                    close_activity_live()
                    close_tool_live()
                    flush_text()
                    err = chunk.get("error") or chunk.get("text") or "Unknown error"
                    self.console.print(Text(f"  {err}", style=f"bold {theme.error.to_rich()}"))
                    self._footer.update(is_active=False, status_message="Failed", show_interrupt=False)
                    return

                if status == "incomplete" and chunk.get("done"):
                    close_activity_live()
                    close_tool_live()
                    if chunk.get("text") and not text_acc:
                        text_acc = chunk["text"]
                    elif text_acc and chunk.get("text"):
                        text_acc += chunk["text"]
                    flush_text()
                    self.console.print(Text("Incomplete.", style=f"bold {theme.warning.to_rich()}"))
                    self._footer.update(is_active=False, status_message="Incomplete", show_interrupt=False)
                    return

                if status == "completed" and chunk.get("done"):
                    close_activity_live()
                    close_tool_live()
                    # Drain any remaining text from the final chunk
                    if chunk.get("text") and not text_acc:
                        text_acc = chunk["text"]
                    elif text_acc and chunk.get("text"):
                        text_acc += chunk["text"]
                    # flush_text stops the transient Live and re-prints text_acc
                    flush_text()
                    self.console.print(Text("Done.", style=f"bold {theme.success.to_rich()}"))
                    await self._refresh_workspace_state()
                    self._update_footer()
                    return

                if chunk.get("text"):
                    close_activity_live()
                    text_acc += chunk["text"]
                    # Use a Live display for incremental streaming text
                    if text_live is None:
                        text_live = Live(
                            Text(text_acc),
                            console=self.console,
                            refresh_per_second=20,
                            transient=True,
                        )
                        _start_live(text_live)
                    else:
                        text_live.update(Text(text_acc))
                    continue

                # advisor_plan: initial Execution Plan — permanent panel so the
                # guidance stays visible in scrollback for the whole task.
                adv_plan = chunk.get("advisor_plan")
                if adv_plan:
                    close_activity_live()
                    self.console.print(Panel(
                        Text(str(adv_plan), style=theme.info.to_rich()),
                        title="🧭 Advisor — Execution Plan",
                        border_style=theme.secondary.to_rich(),
                    ))

                # advisor_feedback: mid-loop advisor memo — print a permanent
                # panel so the guidance stays visible in scrollback, then let
                # the progress_label below update the live activity panel.
                adv_feedback = chunk.get("advisor_feedback")
                if adv_feedback:
                    close_activity_live()
                    self.console.print(Panel(
                        Text(str(adv_feedback), style=theme.info.to_rich()),
                        title="🧭 Advisor Check-in",
                        border_style=theme.secondary.to_rich(),
                    ))

                # progress_label: show in a live activity panel (indexing,
                # classification, planning, etc.) so the user sees what the
                # agent is doing instead of an empty window.
                plabel = chunk.get("progress_label")
                if plabel is not None and not chunk.get("tool_calls") and not chunk.get("tool_result"):
                    if plabel == "":
                        close_activity_live()
                    elif tool_live is not None:
                        update_tool_live(extra=plabel)
                        self._footer.update(status_message=plabel[:40], is_active=True)
                    elif activity_live is not None:
                        activity_label = plabel
                        activity_start_t = time.time()
                        update_activity_live()
                        self._footer.update(status_message=plabel[:40], is_active=True)
                    else:
                        start_activity_live(plabel)
                        self._footer.update(status_message=plabel[:40], is_active=True)
                    continue

                # tool_calls: announce the tool and start a live panel
                if chunk.get("tool_calls"):
                    # Flush any accumulated streaming text first
                    close_activity_live()
                    if text_live is not None:
                        flush_text()
                    for tc in chunk["tool_calls"]:
                        label = _format_tool_args(tc["name"], tc.get("arguments"))
                        path = _tool_path(tc.get("arguments"))
                        # Track for the result handler
                        pending_tool_name = tc["name"]
                        pending_tool_args = tc.get("arguments")
                        # Start the live execution panel
                        if tc["name"] in _EDIT_TOOL_NAMES and path:
                            live_label = f"{tc['name']}({path})"
                        else:
                            live_label = label
                        start_tool_live(live_label)
                        # Keep each completed call distinct in scrollback.
                        self.console.print(Panel(
                            Text(label, style=theme.text_muted.to_rich()),
                            title="Tool Call",
                            border_style=theme.border.to_rich(),
                        ))
                    continue

                # tool_result: stop the live panel, render result with stats
                if chunk.get("tool_result"):
                    close_tool_live()
                    result_text = chunk["tool_result"]
                    _render_tool_result(pending_tool_name, pending_tool_args, result_text)
                    # Reset pending tool tracking
                    pending_tool_name = ""
                    pending_tool_args = None

                if chunk.get("error"):
                    close_tool_live()
                    self.console.print(Text(f"  {chunk['error']}", style=theme.warning.to_rich()))
        finally:
            close_reasoning()
            close_activity_live()
            close_tool_live()
            flush_text()
            self._is_streaming.set(False)
            self._streaming_cancelled = False

        self._footer.update(is_active=False, status_message="idle", show_interrupt=False)

    async def _stream_task(self, task: str, buf: TextBuffer) -> None:
        theme = self._theme_signal.get()
        self._footer.update(is_active=True, status_message="Running task...", show_interrupt=True)

        _spinner_frames = "|/-\\"
        _spinner_idx = 0
        activity_live: Live | None = None
        activity_label = ""
        activity_start_t = 0.0
        activity_refresh_task: asyncio.Task[None] | None = None
        current_model = self._footer.data.model_id

        def _safe_stop(live: Live | None) -> None:
            if live is not None:
                try:
                    live.stop()
                except Exception:
                    pass
                try:
                    self.console._live = None
                except Exception:
                    pass

        def _start_live(live: Live) -> None:
            try:
                live.start()
            except Exception:
                _safe_stop(live)
                live.start()

        def close_activity_live() -> None:
            nonlocal activity_live, activity_label, activity_refresh_task
            if activity_refresh_task is not None:
                activity_refresh_task.cancel()
                activity_refresh_task = None
            _safe_stop(activity_live)
            activity_live = None
            activity_label = ""

        def _activity_panel_content() -> Group:
            nonlocal _spinner_idx
            _spinner_idx = (_spinner_idx + 1) % len(_spinner_frames)
            spinner = _spinner_frames[_spinner_idx]
            elapsed = time.time() - activity_start_t if activity_start_t else 0.0
            lines = [
                Text(f"  {spinner} {activity_label}", style=f"bold {theme.info.to_rich()}"),
                Text(f"    working... {elapsed:.1f}s", style=theme.text_muted.to_rich()),
            ]
            if current_model:
                lines.append(Text(f"    model: {current_model}", style=f"dim {theme.info.to_rich()}"))
            return Group(*lines)

        def start_activity_live(label: str) -> None:
            nonlocal activity_live, activity_label, activity_start_t, activity_refresh_task
            close_activity_live()
            activity_label = label
            activity_start_t = time.time()
            activity_live = Live(
                _activity_panel_content(),
                console=self.console,
                refresh_per_second=10,
                transient=True,
            )
            _start_live(activity_live)
            activity_refresh_task = asyncio.create_task(_refresh_activity_live())

        async def _refresh_activity_live() -> None:
            """Rebuild the dynamic activity panel while a task phase runs."""
            try:
                while activity_live is not None:
                    await asyncio.sleep(0.1)
                    if activity_live is not None:
                        activity_live.update(_activity_panel_content())
            except asyncio.CancelledError:
                pass

        def set_activity_label(label: str) -> None:
            nonlocal activity_label, activity_start_t
            activity_label = label
            activity_start_t = time.time()
            if activity_live is not None:
                activity_live.update(_activity_panel_content())

        self._is_streaming.set(True)
        self._streaming_cancelled = False
        try:
            async for event in self._transport.solve_stream(task):
                if self._streaming_cancelled:
                    break
                phase = event.get("phase", "")
                detail = event.get("detail", "")
                payload = event.get("payload") or {}
                event_model = str(payload.get("model") or "")
                if event_model and event_model != current_model:
                    current_model = event_model
                    self._footer.update(model_id=current_model)
                    if activity_live is not None:
                        activity_live.update(_activity_panel_content())

                if phase == "awaiting_input":
                    close_activity_live()
                    self.console.print(Panel(Text(detail, style=theme.info.to_rich()), title="Plan Required", border_style=theme.border_active.to_rich()))
                    self.console.print(Text("  Type /approve to continue.", style=f"italic {theme.info.to_rich()}"))
                    self._footer.update(is_active=False, status_message="Awaiting approval", show_interrupt=False)
                    return

                if phase in ("task_complete", "done"):
                    close_activity_live()
                    self.console.print(Text(f"  {detail}", style=f"bold {theme.success.to_rich()}"))
                    self._toast_mgr.success("Task complete")
                    self._footer.update(is_active=False, status_message="idle", show_interrupt=False)
                    return

                if phase == "task_incomplete":
                    close_activity_live()
                    self.console.print(Text(f"  {detail}", style=f"bold {theme.warning.to_rich()}"))
                    self._toast_mgr.warning("Task incomplete")
                    self._footer.update(is_active=False, status_message="Incomplete", show_interrupt=False)
                    return

                if phase == "task_failed":
                    close_activity_live()
                    self.console.print(Text(f"  {detail}", style=f"bold {theme.error.to_rich()}"))
                    self._toast_mgr.error(detail)
                    self._footer.update(is_active=False, status_message="Failed", show_interrupt=False)
                    return

                if phase == "plan":
                    close_activity_live()
                    self.console.print(Panel(Text(detail, style=theme.info.to_rich()), title="Plan", border_style=theme.border_active.to_rich()))
                    self._footer.update(status_message="Planning...", is_active=True)
                elif phase in ("status", "start"):
                    label = detail or phase
                    if activity_live is not None:
                        set_activity_label(label)
                    else:
                        start_activity_live(label)
                    self._footer.update(status_message=label[:40], is_active=True)
                elif phase == "advisor":
                    # Initial advisor Execution Plan — permanent record, not a
                    # transient activity label, since the worker follows it for
                    # the rest of the task.
                    close_activity_live()
                    plan_text = str(payload.get("advisor_plan") or detail)
                    self.console.print(Panel(
                        Text(plan_text, style=theme.info.to_rich()),
                        title="🧭 Advisor — Execution Plan",
                        border_style=theme.secondary.to_rich(),
                    ))
                    self._footer.update(status_message="Advisor plan received", is_active=True)
                elif phase == "advisor_checkin":
                    # Mid-loop advisor feedback memo — permanent panel with the
                    # turn number so the user can track advisor interventions.
                    close_activity_live()
                    feedback = str(payload.get("advisor_feedback") or detail)
                    turn_no = payload.get("turn")
                    title = f"🧭 Advisor Check-in (turn {turn_no})" if turn_no else "🧭 Advisor Check-in"
                    self.console.print(Panel(
                        Text(feedback, style=theme.info.to_rich()),
                        title=title,
                        border_style=theme.secondary.to_rich(),
                    ))
                    self._footer.update(status_message="Advisor check-in", is_active=True)
                elif phase == "subagent_progress":
                    agent_id = payload.get("agent_id", "agent")
                    event_detail = payload.get("detail", "")
                    label = f"[{agent_id}] {event_detail or payload.get('phase', '')}"
                    if activity_live is not None:
                        set_activity_label(label)
                    else:
                        start_activity_live(label)
                    self._footer.update(status_message=label[:40], is_active=True)
                elif phase in ("step_complete", "step_failed"):
                    close_activity_live()
                    style = theme.success.to_rich() if phase == "step_complete" else theme.error.to_rich()
                    self.console.print(Text(f"  {detail}", style=f"bold {style}"))
                elif phase == "llm_progress":
                    # Keep the live label truthful during the tool loop -
                    # otherwise it stays stuck on the last pre-loop status
                    # (e.g. "Evaluating whether task needs a plan...").
                    label = detail or "⏳ Generating..."
                    if activity_live is not None:
                        set_activity_label(label)
                    else:
                        start_activity_live(label)
                    self._footer.update(status_message=label[:40], is_active=True)
                elif phase == "tool_call":
                    close_activity_live()
                    self.console.print(Text(f"  [{phase}] {detail}", style=theme.text_muted.to_rich()))
                    start_activity_live(f"⚙ {detail}" if detail else "⚙ Running tool...")
                else:
                    if detail:
                        close_activity_live()
                        self.console.print(Text(f"  [{phase}] {detail}", style=theme.text_muted.to_rich()))
        finally:
            close_activity_live()
            self._is_streaming.set(False)
            self._streaming_cancelled = False

        self._footer.update(is_active=False, status_message="idle", show_interrupt=False)
