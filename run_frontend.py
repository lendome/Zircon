"""Bootstrap script: registers this folder as the 'zirconAgent' package so that
'from zirconAgent.X import Y' works regardless of the repo folder name."""
from __future__ import annotations

import sys
import types
from pathlib import Path

_root = Path(__file__).resolve().parent

# Register this directory as the 'zirconAgent' package so internal imports like
# 'from zirconAgent.core.agent import Agent' resolve correctly even when the
# folder is not named 'zirconAgent'.
_pkg = types.ModuleType("zirconAgent")
_pkg.__path__ = [str(_root)]  # type: ignore[assignment]
_pkg.__package__ = "zirconAgent"
sys.modules["zirconAgent"] = _pkg

# Ensure the parent directory is also on sys.path (launcher.py expects this)
sys.path.insert(0, str(_root.parent))

from zirconAgent.frontend.launcher import main  # noqa: E402

main()
