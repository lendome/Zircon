"""
Raw key reader — cross-platform single-key input.

On Windows: VT byte-stream input (os.read + WaitForSingleObject) with
ENABLE_VIRTUAL_TERMINAL_INPUT when the console supports it (Windows 10+
ConPTY / Windows Terminal), falling back to legacy msvcrt scan codes
otherwise. Set ZIRCON_NO_VT_INPUT=1 to force the legacy path.
On Unix: termios raw mode + os.read(1)

Decodes escape sequences into normalized key names matching the keymap
definitions (e.g. "ctrl+a", "alt+f", "return", "backspace", "up",
"ctrl+right", "shift+home", "ctrl+shift+left").

Modified Enter keys: on Windows consoles, RawTerminal enables
win32-input-mode (CSI ?9001h) so every key event arrives as
CSI Vk;Sc;Uc;Kd;Cs;Ss_ with real modifier state (Shift+Enter ->
"shift+return"). On other platforms it pushes kitty keyboard-protocol
flag 1 (disambiguate) so supporting terminals report Shift/Ctrl/Alt+Enter
as CSI 13;<mod>u; xterm modifyOtherKeys (CSI 27;<mod>;13~) is also
decoded. Ctrl+J / Ctrl+Enter arrive as a bare LF byte and map to "ctrl+j".

Bracketed paste: RawTerminal enables mode 2004 on enter. Pasted payloads
arrive wrapped in \\x1b[200~ ... \\x1b[201~ and are returned as a single
"paste:<payload>" key so multiline pastes never trigger submit.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time

_IS_WINDOWS = sys.platform == "win32"

# Set by RawTerminal when Windows VT input mode is active.
_WINDOWS_VT = False

# Escape sequence -> key name (CSI = \x1b[)
_CSI_MAP: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "1~": "home",
    "4~": "end",
    "5~": "pageup",
    "6~": "pagedown",
    "3~": "delete",
    "2~": "insert",
}

# Final letter -> base key, for modifier-parameterized CSI sequences
# (e.g. "\x1b[1;5C" -> ctrl+right).
_CSI_KEY_BY_FINAL: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
}

# First parameter -> base key, for "~"-terminated sequences
# (e.g. "\x1b[3;5~" -> ctrl+delete).
_TILDE_KEY_BY_PARAM: dict[str, str] = {
    "1": "home",
    "2": "insert",
    "3": "delete",
    "4": "end",
    "5": "pageup",
    "6": "pagedown",
    "7": "home",
    "8": "end",
    "15": "f5",
    "17": "f6",
    "18": "f7",
    "19": "f8",
    "20": "f9",
    "21": "f10",
    "23": "f11",
    "24": "f12",
}

_PASTE_START = "200~"
_PASTE_END = b"\x1b[201~"

_MOUSE_ENABLE = "\x1b[?1002h\x1b[?1006h"
_MOUSE_DISABLE = "\x1b[?1006l\x1b[?1002l"


def enable_mouse_tracking() -> None:
    sys.stdout.write(_MOUSE_ENABLE)
    sys.stdout.flush()


def disable_mouse_tracking() -> None:
    sys.stdout.write(_MOUSE_DISABLE)
    sys.stdout.flush()


def _is_win32_input_prefix(code: str) -> bool:
    """True while code can still be a CSI ?9001h key-event record."""
    return bool(code) and all(char.isdigit() or char == ";" for char in code)


class RawTerminal:
    """Context manager that puts the terminal in raw mode.

    Also enables bracketed paste (mode 2004) on every platform, and
    ENABLE_VIRTUAL_TERMINAL_INPUT on Windows consoles that support it so
    modifier+arrow keys and pastes arrive as VT sequences. Modified Enter
    keys (shift+return etc.) need extra protocols: win32-input-mode
    (CSI ?9001h) on Windows consoles, kitty keyboard flags elsewhere.
    """

    def __init__(self) -> None:
        self._fd = -1
        self._old: list | None = None
        self._win_console: tuple | None = None

    def __enter__(self) -> "RawTerminal":
        global _WINDOWS_VT
        # Hide the terminal's blinking caret on all platforms — the TUI
        # renders its own cursor block in the prompt line, so the native
        # caret below it is a distracting duplicate. Then turn on bracketed
        # paste so the terminal wraps pasted text in \x1b[200~ / \x1b[201~
        # instead of streaming it as keystrokes (which would submit on \n).
        # SGR mouse mode reports click/drag coordinates without the 223-column
        # limit of legacy X10 mouse encoding. Button-event tracking (1002)
        # includes drag motion while avoiding a flood of hover events.
        sys.stdout.write("\x1b[?25l\x1b[?2004h" + _MOUSE_ENABLE)
        if _IS_WINDOWS:
            self._win_console = _enable_windows_vt_input()
            _start_vt_input_watchdog()
        else:
            # Kitty keyboard protocol flag 1 (disambiguate escape codes) so
            # Shift/Ctrl/Alt+Enter arrive as CSI 13;<mod>u instead of a
            # plain \r. Unsupported terminals silently ignore the push.
            sys.stdout.write("\x1b[>1u")
        sys.stdout.flush()
        if _IS_WINDOWS:
            return self
        import termios
        import tty
        self._fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        # setraw also disables output post-processing (OPOST), which stops
        # "\n" translating to "\r\n" — every printed line then starts at the
        # column where the previous one ended, staircasing the whole UI.
        # Re-enable output processing; input stays raw.
        attrs = termios.tcgetattr(self._fd)
        attrs[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(self._fd, termios.TCSADRAIN, attrs)
        return self

    def __exit__(self, *args: object) -> None:
        global _WINDOWS_VT, _WIN_KERNEL32, _WIN_STDIN_HANDLE
        # Always restore the visible caret and disable bracketed paste, even
        # on early returns. Also drop the keyboard enhancement modes pushed
        # on entry (win32-input-mode on Windows, kitty flags elsewhere).
        if _IS_WINDOWS:
            sys.stdout.write(_MOUSE_DISABLE + "\x1b[?2004l\x1b[?25h")
        else:
            sys.stdout.write("\x1b[<u" + _MOUSE_DISABLE + "\x1b[?2004l\x1b[?25h")
        sys.stdout.flush()
        if _IS_WINDOWS:
            _stop_vt_input_watchdog()
            if self._win_console is not None:
                kernel32, handle, old_mode = self._win_console
                try:
                    kernel32.SetConsoleMode(handle, old_mode)
                except Exception:
                    pass
                self._win_console = None
            _WINDOWS_VT = False
            _WIN_KERNEL32 = None
            _WIN_STDIN_HANDLE = None
            return
        if self._old is None:
            return
        import termios
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)


def _enable_windows_vt_input() -> tuple | None:
    """Enable ENABLE_VIRTUAL_TERMINAL_INPUT on the stdin console handle.

    Also clears ENABLE_LINE_INPUT / ENABLE_ECHO_INPUT / ENABLE_PROCESSED_INPUT:
    VT input is a raw byte stream, and leaving line buffering on makes
    ReadFile hold bytes until Enter (which is exactly the kind of desync
    that breaks arrow-key sequences).

    Returns (kernel32, handle, old_mode) for restoration, or None when the
    console doesn't support it (legacy conhost, redirected stdin) — the
    reader then falls back to scan-code decoding.
    """
    global _WINDOWS_VT, _WIN_KERNEL32, _WIN_STDIN_HANDLE
    if os.environ.get("ZIRCON_NO_VT_INPUT"):
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetConsoleMode.restype = ctypes.c_int
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.SetConsoleMode.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel32.WaitForSingleObject.restype = ctypes.c_ulong
        STD_INPUT_HANDLE = -10
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        old_mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(old_mode)):
            return None
        ENABLE_PROCESSED_INPUT = 0x0001
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_ECHO_INPUT = 0x0004
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
        new_mode = (
            (old_mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT)
            & ~ENABLE_LINE_INPUT
            & ~ENABLE_ECHO_INPUT
            & ~ENABLE_PROCESSED_INPUT
        )
        if new_mode != old_mode.value and not kernel32.SetConsoleMode(handle, new_mode):
            return None
        _WINDOWS_VT = True
        _WIN_KERNEL32 = kernel32
        _WIN_STDIN_HANDLE = handle
        return (kernel32, handle, old_mode.value)
    except Exception:
        return None


def _rearm_windows_vt_input() -> None:
    """Re-apply ENABLE_VIRTUAL_TERMINAL_INPUT if a subprocess reset it.

    Child processes spawned by the agent's shell tools inherit the console
    and frequently restore the console input mode on exit (cooked/line
    buffering, echo, processed input). When that happens os.read() on stdin
    blocks until Enter is pressed, so individual keystrokes stop registering
    and the TUI appears frozen. Re-arm VT input on every read so the TUI
    keeps working after such subprocesses; when the mode is already correct
    this is a single cheap GetConsoleMode call.
    """
    if not _WINDOWS_VT:
        return
    k = _WIN_KERNEL32
    h = _WIN_STDIN_HANDLE
    if k is None or h is None:
        return
    try:
        import ctypes
        mode = ctypes.c_ulong()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return
        ENABLE_PROCESSED_INPUT = 0x0001
        ENABLE_LINE_INPUT = 0x0002
        ENABLE_ECHO_INPUT = 0x0004
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
        desired = (
            (mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT)
            & ~ENABLE_PROCESSED_INPUT
            & ~ENABLE_LINE_INPUT
            & ~ENABLE_ECHO_INPUT
        )
        if desired != mode.value:
            k.SetConsoleMode(h, desired)
    except Exception:
        pass


def _start_vt_input_watchdog() -> None:
    """Continuously re-apply ENABLE_VIRTUAL_TERMINAL_INPUT in the background.

    Child processes (git checkpoints, shell tools) that share the TUI console
    frequently restore its input mode to cooked/line-buffered input. If that
    happens while the reader thread is already blocked inside os.read(), the
    once-per-read re-arm in _read_key_windows cannot help: os.read() keeps
    holding keystrokes until Enter, so no key ever arrives to trigger a re-arm
    and the TUI appears frozen. A background watchdog re-arms the mode on a
    short timer so a clobbered console is repaired within ~200ms no matter
    when it happened.
    """
    global _WATCHDOG_THREAD, _WATCHDOG_STOP
    if not _WINDOWS_VT or not _IS_WINDOWS:
        return
    if _WATCHDOG_THREAD is not None and _WATCHDOG_THREAD.is_alive():
        return
    stop = threading.Event()
    _WATCHDOG_STOP = stop

    def _run() -> None:
        while _WINDOWS_VT and not stop.is_set():
            _rearm_windows_vt_input()
            time.sleep(0.2)

    t = threading.Thread(target=_run, name="zircon-vt-rearm", daemon=True)
    _WATCHDOG_THREAD = t
    t.start()


def _stop_vt_input_watchdog() -> None:
    global _WATCHDOG_THREAD, _WATCHDOG_STOP
    if _WATCHDOG_STOP is not None:
        _WATCHDOG_STOP.set()
    _WATCHDOG_THREAD = None
    _WATCHDOG_STOP = None


# Keys already decoded but not yet delivered (e.g. the second press of a
# fast Esc Esc, whose byte arrives inside the first escape's read window).
_pending_keys: list[str] = []


def read_key() -> str:
    """Read a single key press and return its normalized name.

    Returns:
        - Printable chars: the character itself (e.g. "a", "1", "/")
        - Ctrl+letter: "ctrl+a" through "ctrl+z"
        - Alt+key: "alt+<key>"
        - Special keys: "return", "backspace", "escape", "up", "down", etc.
        - Modified keys: "ctrl+left", "shift+home", "ctrl+shift+right", ...
        - Bracketed pastes: "paste:<payload>" (payload may contain newlines)
    """
    if _pending_keys:
        return _pending_keys.pop(0)
    if _IS_WINDOWS:
        return _read_key_windows()
    return _read_key_unix()


# ── Windows ────────────────────────────────────────────────────────────

# Set by _enable_windows_vt_input while VT input mode is active.
_WIN_KERNEL32 = None
_WIN_STDIN_HANDLE = None

# Background console-mode re-arm watchdog (see _start_vt_input_watchdog).
_WATCHDOG_STOP: threading.Event | None = None
_WATCHDOG_THREAD: threading.Thread | None = None


def _win_read_byte(timeout: float | None) -> int | None:
    """Read one byte from the console's VT input stream.

    Uses os.read on the stdin fd — NOT msvcrt. With ENABLE_VIRTUAL_TERMINAL_INPUT
    the console delivers input as a VT byte stream for ReadFile; the CRT's
    _kbhit()/_getch() look for KEY_EVENT records that no longer exist, so
    they desynchronize and shatter escape sequences into stray characters.

    Timed waits use WaitForSingleObject on the console input handle, which
    is signaled whenever input is available. Returns None on timeout;
    timeout=None blocks until a byte arrives.
    """
    fd = sys.stdin.fileno()
    if timeout is None:
        b = os.read(fd, 1)
        return b[0] if b else None
    rc = _WIN_KERNEL32.WaitForSingleObject(_WIN_STDIN_HANDLE, max(1, int(timeout * 1000)))
    if rc != 0:  # WAIT_OBJECT_0 == 0; WAIT_TIMEOUT / WAIT_FAILED
        return None
    b = os.read(fd, 1)
    return b[0] if b else None


def _read_key_windows() -> str:
    # VT input mode: the console speaks a VT byte stream — decode like Unix.
    # With win32-input-mode active every key event arrives as a CSI ... _
    # sequence; key-up events decode to "" and are skipped here.
    if _WINDOWS_VT:
        while True:
            _rearm_windows_vt_input()
            ch = _win_read_byte(None)
            if ch is None:
                raise EOFError
            key = _decode_vt_first_byte(ch, _win_read_byte)
            if key:
                return key

    # Legacy console: scan-code decoding via msvcrt.
    import msvcrt

    ch = msvcrt.getch()

    # msvcrt.getch() returns bytes, not int — convert
    if isinstance(ch, bytes):
        ch = ch[0]

    # Extended key prefix — must check BEFORE printable char (224 >= 32!)
    if ch in (0, 224):
        ch2 = msvcrt.getch()
        if isinstance(ch2, bytes):
            ch2 = ch2[0]
        ext_map: dict[int, str] = {
            72: "up", 80: "down", 77: "right", 75: "left",
            71: "home", 79: "end", 73: "pageup", 81: "pagedown",
            83: "delete", 82: "insert",
            # Ctrl-modified navigation keys (legacy conhost scan codes)
            115: "ctrl+left", 116: "ctrl+right",
            141: "ctrl+up", 145: "ctrl+down",
            119: "ctrl+home", 117: "ctrl+end",
            134: "ctrl+pageup", 118: "ctrl+pagedown",
            147: "ctrl+delete",
        }
        return ext_map.get(ch2, f"ext+{ch2}")

    # Regular printable char
    if ch >= 32 and ch != 127:
        return chr(ch)

    # Control characters
    if ch == 13:
        return "return"
    if ch == 8:
        return "backspace"
    if ch == 27:
        return "escape"
    if ch == 9:
        return "tab"
    # Ctrl+Backspace arrives as 127 in legacy mode
    if ch == 127:
        return "ctrl+backspace"

    # Ctrl+A through Ctrl+Z (1-26)
    if 1 <= ch <= 26:
        return f"ctrl+{chr(ch + 96)}"

    return f"\\x{ch:02x}"


# ── Unix ───────────────────────────────────────────────────────────────


def _read_key_unix() -> str:
    # Read straight from the fd. Buffered sys.stdin slurps whole escape
    # sequences into its internal buffer, so select() on the fd reports no
    # pending bytes and "\x1b[A" decodes as three keys: escape, "[", "A".
    fd = sys.stdin.fileno()
    b = os.read(fd, 1)
    if not b:
        raise EOFError
    ch0 = b[0]

    def read_byte(timeout: float | None) -> int | None:
        import select
        if timeout is not None and not select.select([fd], [], [], timeout)[0]:
            return None
        nb = os.read(fd, 1)
        return nb[0] if nb else None

    return _decode_vt_first_byte(ch0, read_byte)


# ── Shared VT decoding ─────────────────────────────────────────────────


def _decode_vt_first_byte(ch0: int, read_byte) -> str:
    """Decode a key whose first byte is ch0. read_byte(timeout) fetches
    subsequent bytes (None on timeout)."""
    # UTF-8 multibyte character — read the continuation bytes
    if ch0 >= 0x80:
        need = 1 if ch0 < 0xE0 else 2 if ch0 < 0xF0 else 3
        seq = bytes([ch0])
        while len(seq) < 1 + need:
            nb = read_byte(0.05)
            if nb is None:
                break
            seq += bytes([nb])
        try:
            return seq.decode("utf-8")
        except UnicodeDecodeError:
            return "\\x" + seq.hex()

    ch = chr(ch0)
    if ch == "\r":
        return "return"
    # Ctrl+J / Ctrl+Enter send LF (Enter itself sends CR), so map it to the
    # key name the keymap binds for newline insertion.
    if ch == "\n":
        return "ctrl+j"
    if ch == "\x7f":
        return "backspace"
    # macOS terminals commonly encode Ctrl+Backspace as BS while the normal
    # Backspace key emits DEL. They are distinguishable in this raw stream.
    if ch == "\x08":
        return "ctrl+backspace"
    if ch == "\x1b":
        return _read_escape_sequence(read_byte)

    if ch == "\t":
        return "tab"
    if "\x01" <= ch <= "\x1a":
        return f"ctrl+{chr(ord(ch) + 96)}"

    if ch0 >= 32:
        return ch

    return f"\\x{ch0:02x}"


def _read_escape_sequence(read_byte) -> str:
    """Decode the bytes after an initial \\x1b: CSI/SS3 sequence, Alt+key,
    or a plain Escape press if nothing follows."""
    b1 = read_byte(0.05)
    if b1 is None:
        return "escape"
    c1 = chr(b1)

    if c1 == "\x1b":
        # A second Escape pressed fast enough to land in this read window:
        # deliver two distinct escape keys, not a bogus "alt+\x1b".
        _pending_keys.append("escape")
        return "escape"

    if c1 in ("[", "O"):
        # CSI (\x1b[) or SS3 (\x1bO): read through a real final byte. A
        # win32-input-mode record has numeric fields followed by "_", and its
        # Unicode codepoint can be an ASCII letter, so do not end it early.
        code = ""
        while True:
            nb = read_byte(0.05)
            if nb is None:
                break
            nc = chr(nb)
            code += nc
            if nc in ("~", "_") or (nc.isalpha() and not _is_win32_input_prefix(code[:-1])):
                break
        return _decode_csi(code, read_byte)

    # Option+Backspace on Terminal.app/iTerm2 is Meta-DEL (ESC DEL).
    if b1 in (0x7F, 0x08):
        return "alt+backspace"

    # Alt+key: \x1b<key>
    return f"alt+{c1}"


_CSI_PARAM_RE = re.compile(r"^(\d*)(?:;(\d+))?([A-Za-z~])$")

# Kitty keyboard protocol: CSI <keycode>;<modifier>u (e.g. 13;2u = shift+return)
_CSI_U_RE = re.compile(r"^(\d+)(?:;(\d+))?u$")

# xterm modifyOtherKeys: CSI 27;<modifier>;<keycode>~ (e.g. 27;2;13~ = shift+return)
_CSI_MODIFY_OTHER_KEYS_RE = re.compile(r"^27;(\d+);(\d+)~$")

# Some macOS/rxvt configurations omit the leading "1;" and emit CSI 5D
# rather than CSI 1;5D for Ctrl+Left.
_CSI_SHORT_MODIFIER_RE = re.compile(r"^([2-8])([ABCD])$")

# SGR mouse: CSI <button;column;row M (press/move) or m (release).
_SGR_MOUSE_RE = re.compile(r"^<(\d+);(\d+);(\d+)([Mm])$")

# win32-input-mode: CSI Vk;Sc;Uc;Kd;Cs;Ss_ — ConPTY/conhost encode every
# console key event with full modifier state (e.g. 13;28;13;1;16;1_ =
# shift+return). Vk = virtual-key code, Sc = scan code, Uc = Unicode char,
# Kd = 1 key-down / 0 key-up, Cs = control-key-state bits, Ss = event type.
_WIN32_INPUT_RE = re.compile(r"^(\d+)(?:;(\d*))?(?:;(\d*))?(?:;(\d*))?(?:;(\d*))?(?:;(\d*))?_$")

# win32 control-key-state bits
_WIN_CS_ALT = 0x03   # LEFT_ALT_PRESSED | RIGHT_ALT_PRESSED
_WIN_CS_CTRL = 0x0C  # LEFT_CTRL_PRESSED | RIGHT_CTRL_PRESSED
_WIN_CS_SHIFT = 0x10

# Virtual-key code -> base key, for win32-input-mode events with no
# useful character (Uc = 0)
_WIN32_VK_MAP: dict[int, str] = {
    33: "pageup", 34: "pagedown", 35: "end", 36: "home",
    37: "left", 38: "up", 39: "right", 40: "down",
    45: "insert", 46: "delete",
    112: "f1", 113: "f2", 114: "f3", 115: "f4", 116: "f5", 117: "f6",
    118: "f7", 119: "f8", 120: "f9", 121: "f10", 122: "f11", 123: "f12",
}

# Keycode -> base key for CSI u / modifyOtherKeys sequences
_KEYCODE_MAP: dict[str, str] = {
    "9": "tab",
    "13": "return",
    "27": "escape",
    "32": "space",
    "127": "backspace",
}


def _decode_csi(code: str, read_byte) -> str:
    """Decode a CSI/SS3 parameter string (the bytes between \\x1b[ and the
    final byte) into a normalized key name, including modifier params."""
    # Bracketed paste start — swallow the payload through \x1b[201~
    if code == _PASTE_START:
        return "paste:" + _read_paste_payload(read_byte)

    mouse = _SGR_MOUSE_RE.match(code)
    if mouse:
        button_code, column, row, final = mouse.groups()
        code_value = int(button_code)
        button = code_value & 0b11
        if code_value & 64:
            action = "wheel_down" if code_value & 1 else "wheel_up"
        elif final == "m":
            action = "up"
        elif code_value & 32:
            action = "drag"
        else:
            action = "down"
        return f"mouse:{action}:{button}:{column}:{row}"

    # Shift+Tab (backtab)
    if code == "Z":
        return "shift+tab"

    # win32-input-mode: CSI Vk;Sc;Uc;Kd;Cs;Ss_
    m = _WIN32_INPUT_RE.match(code)
    if m:
        g = m.groups()
        vk = int(g[0])
        uc = int(g[2]) if g[2] else 0
        kd = int(g[3]) if g[3] else 1
        cs = int(g[4]) if g[4] else 0
        return _decode_win32_input(vk, uc, kd, cs, read_byte)

    # Kitty keyboard protocol: CSI <keycode>;<modifier>u
    m = _CSI_U_RE.match(code)
    if m:
        keycode, mod_s = m.groups()
        base = _KEYCODE_MAP.get(keycode)
        if base is not None:
            mods = _decode_modifier(int(mod_s)) if mod_s else []
            if mods:
                return "+".join([*mods, base])
            return base

    # xterm modifyOtherKeys: CSI 27;<modifier>;<keycode>~
    m = _CSI_MODIFY_OTHER_KEYS_RE.match(code)
    if m:
        mod_s, keycode = m.groups()
        base = _KEYCODE_MAP.get(keycode)
        if base is not None:
            mods = _decode_modifier(int(mod_s))
            if mods:
                return "+".join([*mods, base])
            return base

    # Unmodified keys
    if code in _CSI_MAP:
        return _CSI_MAP[code]

    short_modifier = _CSI_SHORT_MODIFIER_RE.match(code)
    if short_modifier:
        mod_s, final = short_modifier.groups()
        base = _CSI_KEY_BY_FINAL[final]
        return "+".join([*_decode_modifier(int(mod_s)), base])

    # Modifier-parameterized: e.g. "1;5C" (ctrl+right), "1;2A" (shift+up),
    # "1;6D" (ctrl+shift+left), "3;5~" (ctrl+delete)
    m = _CSI_PARAM_RE.match(code)
    if m:
        param_s, mod_s, final = m.groups()
        if final == "~":
            base = _TILDE_KEY_BY_PARAM.get(param_s)
        else:
            base = _CSI_KEY_BY_FINAL.get(final)
        if base is not None:
            mods = _decode_modifier(int(mod_s)) if mod_s else []
            if mods:
                return "+".join([*mods, base])
            return base

    return f"csi+{code}"


def _decode_modifier(n: int) -> list[str]:
    """XTerm modifier parameter: value-1 is a bitmask of
    shift(1) + alt(2) + ctrl(4)."""
    bits = n - 1
    mods: list[str] = []
    if bits & 4:
        mods.append("ctrl")
    if bits & 2:
        mods.append("alt")
    if bits & 1:
        mods.append("shift")
    return mods


def _win_mods(ctrl: bool, alt: bool, shift: bool) -> list[str]:
    mods: list[str] = []
    if ctrl:
        mods.append("ctrl")
    if alt:
        mods.append("alt")
    if shift:
        mods.append("shift")
    return mods


def _decode_win32_input(vk: int, uc: int, kd: int, cs: int, read_byte) -> str:
    """Decode one win32-input-mode key event into a normalized key name.

    Returns "" for events that must be skipped (key-up, dead keys,
    modifier-only presses) — the reader loop keeps reading in that case.
    """
    if not kd:
        return ""
    shift = bool(cs & _WIN_CS_SHIFT)
    ctrl = bool(cs & _WIN_CS_CTRL)
    alt = bool(cs & _WIN_CS_ALT)

    if vk == 13:  # VK_RETURN — modifier state survives here, unlike a bare \r
        mods = _win_mods(ctrl, alt, shift)
        return "+".join([*mods, "return"]) if mods else "return"
    if vk == 8:  # VK_BACK
        return "ctrl+backspace" if ctrl else "backspace"
    if vk == 9:  # VK_TAB
        mods = _win_mods(ctrl, alt, shift)
        return "+".join([*mods, "tab"]) if mods else "tab"
    if vk == 27:  # VK_ESCAPE — could also be the ESC leading a paste/CSI
        return _win32_escape_or_sequence(read_byte)

    base = _WIN32_VK_MAP.get(vk)
    if base is not None:
        mods = _win_mods(ctrl, alt, shift)
        return "+".join([*mods, base]) if mods else base

    if uc:
        if uc == 10:
            return "ctrl+j"
        if uc == 13:
            return "return"
        if 1 <= uc <= 26:
            return f"ctrl+{chr(uc + 96)}"
        c = chr(uc)
        if uc >= 32:
            return f"alt+{c}" if alt else c
        return f"\\x{uc:02x}"

    # Modifier-only press (Shift/Ctrl/Alt), dead key, etc.
    return ""


def _win32_next_char(read_byte, timeout: float | None) -> str | None:
    """Read one win32-input-mode sequence from the byte stream and return
    the character it carries (its Uc field). Key-up and keyless events are
    skipped. Returns None on timeout."""
    while True:
        b = read_byte(timeout)
        if b is None:
            return None
        if b != 0x1B:
            # Defensive: a stray raw byte (mode toggled mid-stream)
            return chr(b)
        b2 = read_byte(0.05)
        if b2 is None or chr(b2) != "[":
            return None
        code = ""
        while True:
            nb = read_byte(0.05)
            if nb is None:
                return None
            nc = chr(nb)
            if nc == "_":
                break
            code += nc
        parts = code.split(";")
        try:
            uc = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            kd = int(parts[3]) if len(parts) > 3 and parts[3] else 1
        except ValueError:
            return None
        if kd and uc:
            return chr(uc)
        # key-up or keyless event — keep looking


def _win32_escape_or_sequence(read_byte) -> str:
    """A VK_ESCAPE event arrived in win32-input-mode. It may be a real
    Escape press, or the ESC byte leading a bracketed paste / CSI sequence
    (indistinguishable at this point) — peek at what follows."""
    c = _win32_next_char(read_byte, 0.05)
    if c is None:
        return "escape"
    if c == "\x1b":
        _pending_keys.append("escape")
        return "escape"
    if c in ("[", "O"):
        code = ""
        while True:
            c = _win32_next_char(read_byte, 0.05)
            if c is None:
                break
            code += c
            if c in ("~", "_") or (c.isalpha() and not _is_win32_input_prefix(code[:-1])):
                break
        if code == _PASTE_START:
            return "paste:" + _win32_read_paste(read_byte)
        return _decode_csi(code, read_byte)
    return f"alt+{c}"


def _win32_read_paste(read_byte) -> str:
    """Collect a bracketed-paste payload whose bytes arrive as the Uc field
    of win32-input-mode sequences, up to the \\x1b[201~ terminator."""
    data = ""
    while True:
        c = _win32_next_char(read_byte, 5.0)
        if c is None:
            break
        data += c
        if data.endswith("\x1b[201~"):
            data = data[: -len("\x1b[201~")]
            break
    return data


def _read_paste_payload(read_byte) -> str:
    """Read a bracketed-paste payload up to the \\x1b[201~ terminator."""
    data = bytearray()
    term_len = len(_PASTE_END)
    while True:
        nb = read_byte(5.0)
        if nb is None:
            break
        data.append(nb)
        if len(data) >= term_len and bytes(data[-term_len:]) == _PASTE_END:
            del data[-term_len:]
            break
    return data.decode("utf-8", errors="replace")


def is_printable(key: str) -> bool:
    """True if the key is a single printable character."""
    return len(key) == 1 and ord(key) >= 32
