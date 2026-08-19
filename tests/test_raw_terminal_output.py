"""RawTerminal must keep "\n" -> "\r\n" output translation (no staircase)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

_CHILD_CODE = """
import sys
from zirconAgent.cli.tui.input.key_reader import RawTerminal
with RawTerminal():
    sys.stdout.write("first\\nsecond\\n")
    sys.stdout.flush()
"""


@unittest.skipIf(sys.platform == "win32", "pty is Unix-only")
class TestRawTerminalOutput(unittest.TestCase):
    def test_newlines_carriage_return_in_raw_mode(self) -> None:
        import pty

        master, slave = pty.openpty()
        pid = os.fork()
        if pid == 0:
            # Child: make the pty the controlling stdio and run the snippet
            os.setsid()
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            os.close(master)
            os.close(slave)
            os.execv(
                sys.executable,
                [sys.executable, "-c", _CHILD_CODE],
            )

        os.close(slave)
        chunks = []
        try:
            while True:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
        finally:
            os.close(master)
            os.waitpid(pid, 0)

        output = b"".join(chunks).decode(errors="replace")
        self.assertIn("first\r\nsecond\r\n", output)


if __name__ == "__main__":
    unittest.main()
