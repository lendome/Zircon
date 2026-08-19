"""read_key must decode escape sequences as single keys, not byte-by-byte."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

# (bytes to feed, expected decoded key)
_CASES = [
    (b"\x1b[A", "up"),
    (b"\x1b[B", "down"),
    (b"\x1b[5~", "pageup"),
    (b"\x1b[3~", "delete"),
    (b"\x1bOA", "up"),  # SS3 / application cursor mode
    (b"\x1bf", "alt+f"),
    (b"\x1b", "escape"),
    (b"q", "q"),
    (b"\x03", "ctrl+c"),
    ("é".encode(), "é"),
]

def _run_in_pty(child_code: str, writes: list[bytes], expect_lines: int) -> list[str]:
    """Run child_code in a pty, feed it writes, return its output lines."""
    import pty

    master, slave = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(master)
        os.close(slave)
        os.execv(sys.executable, [sys.executable, "-c", child_code])

    os.close(slave)
    try:
        time.sleep(0.5)  # let the child enter raw mode
        for data in writes:
            os.write(master, data)
            # Gap so a bare escape isn't glued to the next sequence
            time.sleep(0.1)

        out = b""
        deadline = time.time() + 5
        while time.time() < deadline and out.count(b"\n") < expect_lines:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    finally:
        os.close(master)
        os.waitpid(pid, 0)

    lines = out.decode(errors="replace").splitlines()
    return [line for line in lines if line.startswith("'")]


def _child_reading(n: int) -> str:
    return f"""
import sys
sys.path.insert(0, {str(_PARENT)!r})
from zirconAgent.cli.tui.input.key_reader import RawTerminal, read_key
with RawTerminal():
    for _ in range({n}):
        sys.stdout.write(repr(read_key()) + "\\n")
        sys.stdout.flush()
"""


@unittest.skipIf(sys.platform == "win32", "pty is Unix-only")
class TestKeyReader(unittest.TestCase):
    def test_escape_sequences_decode_as_single_keys(self) -> None:
        decoded = _run_in_pty(
            _child_reading(len(_CASES)),
            [data for data, _ in _CASES],
            expect_lines=len(_CASES),
        )
        self.assertEqual(decoded, [repr(exp) for _, exp in _CASES])

    def test_fast_double_escape_is_two_escapes(self) -> None:
        # Both escape bytes land in one read window — must not become alt+\x1b
        decoded = _run_in_pty(
            _child_reading(2),
            [b"\x1b\x1b"],
            expect_lines=2,
        )
        self.assertEqual(decoded, ["'escape'", "'escape'"])


class TestModifiedEnterDecoding(unittest.TestCase):
    """Shift/Ctrl/Alt+Enter decode as modified return keys (no pty needed)."""

    @staticmethod
    def _rb(*_bytes: int):
        it = iter(_bytes)
        return lambda timeout=None: next(it, None)

    def _csi(self, code: str) -> str:
        from zirconAgent.cli.tui.input.key_reader import _decode_csi
        return _decode_csi(code, self._rb())

    def test_kitty_csi_u_enter(self) -> None:
        self.assertEqual(self._csi("13u"), "return")
        self.assertEqual(self._csi("13;2u"), "shift+return")
        self.assertEqual(self._csi("13;5u"), "ctrl+return")
        self.assertEqual(self._csi("13;3u"), "alt+return")
        self.assertEqual(self._csi("13;6u"), "ctrl+shift+return")

    def test_modify_other_keys_enter(self) -> None:
        self.assertEqual(self._csi("27;2;13~"), "shift+return")
        self.assertEqual(self._csi("27;5;13~"), "ctrl+return")
        self.assertEqual(self._csi("27;3;13~"), "alt+return")

    def test_existing_sequences_unaffected(self) -> None:
        self.assertEqual(self._csi("A"), "up")
        self.assertEqual(self._csi("1;5C"), "ctrl+right")
        self.assertEqual(self._csi("Z"), "shift+tab")
        self.assertEqual(self._csi("3;5~"), "ctrl+delete")

    def test_macos_navigation_sequences(self) -> None:
        self.assertEqual(self._csi("1;5D"), "ctrl+left")
        self.assertEqual(self._csi("1;5C"), "ctrl+right")
        self.assertEqual(self._csi("1;3D"), "alt+left")
        self.assertEqual(self._csi("1;3C"), "alt+right")
        self.assertEqual(self._csi("1;4D"), "alt+shift+left")
        self.assertEqual(self._csi("1;4C"), "alt+shift+right")
        self.assertEqual(self._csi("5D"), "ctrl+left")
        self.assertEqual(self._csi("5C"), "ctrl+right")

    def test_macos_backspace_sequences(self) -> None:
        from zirconAgent.cli.tui.input.key_reader import (
            _decode_vt_first_byte,
            _read_escape_sequence,
        )

        self.assertEqual(_decode_vt_first_byte(0x7F, self._rb()), "backspace")
        self.assertEqual(_decode_vt_first_byte(0x08, self._rb()), "ctrl+backspace")
        self.assertEqual(_read_escape_sequence(self._rb(0x7F)), "alt+backspace")
        self.assertEqual(self._csi("127;5u"), "ctrl+backspace")

    def test_sgr_mouse_sequences(self) -> None:
        self.assertEqual(self._csi("<0;12;4M"), "mouse:down:0:12:4")
        self.assertEqual(self._csi("<32;15;4M"), "mouse:drag:0:15:4")
        self.assertEqual(self._csi("<0;15;4m"), "mouse:up:0:15:4")
        self.assertEqual(self._csi("<64;15;4M"), "mouse:wheel_up:0:15:4")
        self.assertEqual(self._csi("<65;15;4M"), "mouse:wheel_down:1:15:4")

    def test_lf_byte_is_ctrl_j(self) -> None:
        from zirconAgent.cli.tui.input.key_reader import _decode_vt_first_byte
        self.assertEqual(_decode_vt_first_byte(10, self._rb()), "ctrl+j")
        self.assertEqual(_decode_vt_first_byte(13, self._rb()), "return")


class TestWin32InputMode(unittest.TestCase):
    """win32-input-mode (CSI Vk;Sc;Uc;Kd;Cs;Ss_) decoding — the Windows
    console path that carries real modifier state for Shift+Enter."""

    @staticmethod
    def _rb(*_bytes: int):
        it = iter(_bytes)
        return lambda timeout=None: next(it, None)

    @staticmethod
    def _w32(vk: int, uc: int, kd: int = 1, cs: int = 0) -> str:
        return f"{vk};1;{uc};{kd};{cs};1_"

    def _csi(self, code: str, *feed: int) -> str:
        from zirconAgent.cli.tui.input.key_reader import _decode_csi
        return _decode_csi(code, self._rb(*feed))

    def test_enter_modifiers(self) -> None:
        self.assertEqual(self._csi(self._w32(13, 13)), "return")
        self.assertEqual(self._csi(self._w32(13, 13, cs=0x10)), "shift+return")
        self.assertEqual(self._csi(self._w32(13, 13, cs=0x08)), "ctrl+return")
        self.assertEqual(self._csi(self._w32(13, 13, cs=0x02)), "alt+return")

    def test_key_up_is_skipped(self) -> None:
        self.assertEqual(self._csi(self._w32(13, 13, kd=0)), "")

    def test_chars_and_ctrl(self) -> None:
        self.assertEqual(self._csi(self._w32(67, 99)), "c")
        self.assertEqual(self._csi(self._w32(67, 3, cs=0x08)), "ctrl+c")
        self.assertEqual(self._csi(self._w32(74, 10, cs=0x08)), "ctrl+j")

    def test_special_keys_with_modifiers(self) -> None:
        self.assertEqual(self._csi(self._w32(38, 0)), "up")
        self.assertEqual(self._csi(self._w32(38, 0, cs=0x10)), "shift+up")
        self.assertEqual(self._csi(self._w32(37, 0, cs=0x08)), "ctrl+left")
        self.assertEqual(self._csi(self._w32(9, 9, cs=0x10)), "shift+tab")
        self.assertEqual(self._csi(self._w32(8, 8, cs=0x08)), "ctrl+backspace")
        self.assertEqual(self._csi(self._w32(112, 0)), "f1")
        self.assertEqual(self._csi(self._w32(16, 0)), "")  # Shift alone

    def test_escape_and_paste(self) -> None:
        # Lone Escape: peek times out
        self.assertEqual(self._csi(self._w32(27, 27)), "escape")
        # Bracketed paste whose bytes arrive as win32 sequence chars
        payload = "[200~hello\nworld\x1b[201~"
        stream = b"".join(
            f"\x1b[0;1;{ord(c)};1;0;1_".encode() for c in payload
        )
        self.assertEqual(
            self._csi(self._w32(27, 27), *stream), "paste:hello\nworld"
        )

    def test_underscore_final_in_escape_reader(self) -> None:
        from zirconAgent.cli.tui.input.key_reader import _read_escape_sequence
        seq = b"[13;28;13;1;16;1_"
        self.assertEqual(_read_escape_sequence(self._rb(*seq)), "shift+return")

    def test_printable_unicode_field_does_not_split_win32_record(self) -> None:
        from zirconAgent.cli.tui.input.key_reader import _read_escape_sequence
        # Uc is an ASCII letter (71 == "G"). The reader must wait for the
        # trailing underscore rather than treating that letter as a CSI final.
        seq = b"[71;34;71;1;16;1_"
        self.assertEqual(_read_escape_sequence(self._rb(*seq)), "G")


if __name__ == "__main__":
    unittest.main()
