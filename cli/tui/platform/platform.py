"""
Platform detection — Windows ConPTY, terminal multiplexer, display server.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass
class TerminalEnvironment:
    """Detected terminal environment."""

    platform: str = ""        # win32 | darwin | linux
    multiplexer: str | None = None  # tmux | screen | None
    display_server: str | None = None  # wayland | x11 | None
    is_windows: bool = False
    is_macos: bool = False
    is_linux: bool = False
    supports_suspend: bool = False
    supports_mouse: bool = True

    @property
    def needs_shell_spawn(self) -> bool:
        """True if process spawning needs shell=True (Windows)."""
        return self.is_windows

    @property
    def paste_uses_cr_only(self) -> bool:
        """True if paste may send CR-only newlines (Windows ConPTY)."""
        return self.is_windows


def detect_terminal_environment() -> TerminalEnvironment:
    """Detect the platform, terminal multiplexer, and display server."""
    platform = sys.platform
    multiplexer = None
    display_server = None

    if os.environ.get("TMUX"):
        multiplexer = "tmux"
    elif os.environ.get("STY"):
        multiplexer = "screen"

    if os.environ.get("WAYLAND_DISPLAY"):
        display_server = "wayland"
    elif os.environ.get("DISPLAY"):
        display_server = "x11"

    is_win = platform == "win32"
    is_mac = platform == "darwin"
    is_lin = platform == "linux"

    return TerminalEnvironment(
        platform=platform,
        multiplexer=multiplexer,
        display_server=display_server,
        is_windows=is_win,
        is_macos=is_mac,
        is_linux=is_lin,
        supports_suspend=not is_win,
        supports_mouse=True,
    )


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform == "linux"


_saved_console_mode: int | None = None
_saved_output_mode: int | None = None

# ENABLE_VIRTUAL_TERMINAL_PROCESSING — makes conhost interpret ANSI/VT
# escape sequences instead of printing them literally. Windows Terminal
# has this on by default; classic conhost (e.g. Windows PowerShell 5.1)
# does NOT, so the renderer must enable it explicitly.
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


def enable_win32_vt_output() -> bool:
    """Enable VT escape processing on the stdout console handle.

    Saves the original output mode so restore_win32_console_mode() can put
    it back on exit. Returns True if the console accepts VT sequences
    (either enabled here or already a VT-capable terminal), False if the
    console is a legacy one that refuses — in which case rendering must
    degrade rather than spew raw escapes.

    Idempotent: safe to call repeatedly (e.g. after subprocesses mutate
    the shared console mode). On the first call the original mode is
    captured; later calls only re-apply the VT bit.
    """
    global _saved_output_mode
    if not is_windows():
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if not handle:
            return False
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # Not a real console (piped/redirected output): escapes are
            # consumed by whatever reads the pipe, nothing to enable.
            return True
        if _saved_output_mode is None:
            _saved_output_mode = mode.value
        desired = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if desired != mode.value and not kernel32.SetConsoleMode(handle, ctypes.c_ulong(desired)):
            return False
        return True
    except Exception:
        return False


def win32_vt_output_supported() -> bool:
    """True if the stdout console already processes VT sequences."""
    if not is_windows():
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_ulong()
        if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True  # piped — not a conhost rendering problem
        return bool(mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        return False


def disable_win32_processed_input() -> None:
    """Disable Windows ConPTY processed input for raw key events.

    Saves the original console mode so it can be restored on exit via
    restore_win32_console_mode().

    WARNING: only call this if the app reads raw key events. If the app
    uses Python's input() (cooked/line mode), do NOT call this — it
    breaks backspace and other line-editing keys.
    """
    global _saved_console_mode
    if not is_windows():
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_PROCESSED_INPUT = 0x0001
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        _saved_console_mode = mode.value
        mode.value &= ~ENABLE_PROCESSED_INPUT
        kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass


def restore_win32_console_mode() -> None:
    """Restore the original Windows console modes saved before modification."""
    global _saved_console_mode, _saved_output_mode
    if not is_windows():
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        if _saved_console_mode is not None:
            STD_INPUT_HANDLE = -10
            handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            kernel32.SetConsoleMode(handle, ctypes.c_ulong(_saved_console_mode))
    except Exception:
        pass
    _saved_console_mode = None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        if _saved_output_mode is not None:
            STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            kernel32.SetConsoleMode(handle, ctypes.c_ulong(_saved_output_mode))
    except Exception:
        pass
    _saved_output_mode = None


def flush_win32_input_buffer() -> None:
    """Flush the Windows input buffer on exit."""
    if not is_windows():
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        kernel32.FlushConsoleInputBuffer(handle)
    except Exception:
        pass
