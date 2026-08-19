from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    _parent = str(Path(__file__).resolve().parent.parent)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    from zirconAgent.cli import main  # type: ignore[no-redef]
else:
    from .cli import main

main()
