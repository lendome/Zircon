"""Quick key-reader diagnostic: prints decoded key names.

Run from the repo parent (the folder containing zirconAgent/):

    python zirconAgent/tests/manual_key_check.py

Press keys to see their decoded names. Try Shift+Enter, Ctrl+Enter,
Ctrl+J, arrows, and paste some text. Press Ctrl+C twice to exit.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from zirconAgent.cli.tui.input.key_reader import RawTerminal, read_key


def main() -> None:
    print("Key check — press keys (Ctrl+C twice to exit)")
    with RawTerminal():
        while True:
            try:
                key = read_key()
            except (EOFError, KeyboardInterrupt):
                break
            sys.stdout.write(f"\r\n{key!r}")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
