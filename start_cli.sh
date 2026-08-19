#!/bin/bash
# =============================================================================
#  Zircon - Coding Agent
#  CLI startup script (macOS/Linux)
#  Launches the Rich TUI chat interface
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ███████╗██╗██████╗  ██████╗ ██████╗ ███╗   ██╗"
echo "  ╚══███╔╝██║██╔══██╗██╔════╝██╔═══██╗████╗  ██║"
echo "    ███╔╝ ██║██████╔╝██║     ██║   ██║██╔██╗ ██║"
echo "   ███╔╝  ██║██╔══██╗██║     ██║   ██║██║╚██╗██║"
echo "  ███████╗██║██║  ██║╚██████╗╚██████╔╝██║ ╚████║"
echo "  ╚══════╝╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝"
echo "  Zircon v1.0 — Autonomous Coding Agent"
echo ""

# Check Python exists
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  [ERROR] Python is not installed or not in PATH."
    echo "  Install Python 3.10+ from https://www.python.org/downloads/"
    read -rp "  Press Enter to exit..." _
    exit 1
fi

# Check Python version
PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "  [ERROR] Python 3.10+ required, found $PY_VERSION"
    read -rp "  Press Enter to exit..." _
    exit 1
fi

echo "  Python: $PYTHON ($PY_VERSION)"
echo ""

# Install/check dependencies
if [ ! -f "requirements.txt" ]; then
    echo "  [ERROR] requirements.txt not found in $SCRIPT_DIR"
    read -rp "  Press Enter to exit..." _
    exit 1
fi

echo "  Checking dependencies..."
$PYTHON -m pip install -q -r requirements.txt 2>/dev/null || {
    echo "  [WARN] pip install had issues, attempting to continue..."
}

# Launch Zircon CLI TUI
echo ""
echo "  Starting Zircon CLI..."
echo ""

$PYTHON -m zirconAgent "$@"

echo ""
echo "  Zircon closed."