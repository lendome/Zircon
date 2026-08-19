from __future__ import annotations

import sys
import types
import threading
import webbrowser
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PARENT_DIR = _PROJECT_ROOT.parent

# Register the project root as the 'zirconAgent' package so that
# 'from zirconAgent.X import Y' works regardless of the folder name.
if "zirconAgent" not in sys.modules:
    _pkg = types.ModuleType("zirconAgent")
    _pkg.__path__ = [str(_PROJECT_ROOT)]  # type: ignore[assignment]
    _pkg.__package__ = "zirconAgent"
    sys.modules["zirconAgent"] = _pkg

if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))


def main():
    from zirconAgent.frontend.app import start_server

    port = 5555

    start_server(port=port, debug=False)
    print(f"  \033[96m[ZIRCON]\033[0m Flask server running on http://127.0.0.1:{port}")

    try:
        import webview
    except ImportError:
        print("  \033[93m[WARN]\033[0m pywebview not installed — opening in browser.")
        webbrowser.open(f"http://127.0.0.1:{port}")
        print(f"  \033[96m[ZIRCON]\033[0m Running at http://127.0.0.1:{port} — press Ctrl+C to stop.")
        threading.Event().wait()
        return

    from zirconAgent.frontend import app as _app

    _window = webview.create_window(
        title="ZIRCON v1.0 — Coding Agent",
        url=f"http://127.0.0.1:{port}",
        width=1200,
        height=800,
        min_size=(900, 600),
        resizable=True,
        frameless=True,
        text_select=True,
        easy_drag=False,
    )
    _app._webview_window = _window
    try:
        _window.icon = str(_PROJECT_ROOT / "frontend" / "static" / "icon-64.png")
    except Exception:
        pass

    webview.start(
        debug=False,
        http_server=False,  # We serve via Flask
        private_mode=False,
    )


if __name__ == "__main__":
    main()