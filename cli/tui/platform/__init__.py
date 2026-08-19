"""
Cross-platform & terminal compatibility.

Handles platform differences: Windows ConPTY, terminal multiplexer
detection, display server detection, paste normalization, terminal
suspend, SIGHUP, path normalization, and platform-conditional keybinds.
"""

from __future__ import annotations

from .platform import (
    TerminalEnvironment,
    detect_terminal_environment,
    is_windows,
    is_macos,
    is_linux,
    disable_win32_processed_input,
    restore_win32_console_mode,
    flush_win32_input_buffer,
)
from .paste import normalize_paste, normalize_prompt_content
from .paths import normalize_mention_path, pasted_filepath
from .suspend import suspend_terminal, supports_suspend
from .sighup import SighupHandler
from .fetch import graceful_fetch

__all__ = [
    "TerminalEnvironment",
    "detect_terminal_environment",
    "is_windows",
    "is_macos",
    "is_linux",
    "disable_win32_processed_input",
    "restore_win32_console_mode",
    "flush_win32_input_buffer",
    "normalize_paste",
    "normalize_prompt_content",
    "normalize_mention_path",
    "pasted_filepath",
    "suspend_terminal",
    "supports_suspend",
    "SighupHandler",
    "graceful_fetch",
]
