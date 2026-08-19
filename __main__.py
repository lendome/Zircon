from __future__ import annotations

import sys
import types
from pathlib import Path

if __package__ is None or __package__ == "":
    _root = Path(__file__).resolve().parent
    _package = types.ModuleType("zirconAgent")
    _package.__file__ = str(_root / "__init__.py")
    _package.__package__ = "zirconAgent"
    _package.__path__ = [str(_root)]  # type: ignore[attr-defined]
    sys.modules["zirconAgent"] = _package
    from zirconAgent.cli import main  # type: ignore[no-redef]
else:
    from .cli import main

main()
