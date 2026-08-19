#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${ZIRCON_INSTALL_WORKSPACE:-$PWD}"

if [ ! -f "$PROJECT_ROOT/cli/tui/__init__.py" ]; then
    echo "[ERROR] Zircon TUI was not found under $PROJECT_ROOT/cli/tui." >&2
    exit 1
fi

PYTHON=""
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$(command -v "$candidate")"
            break
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python 3.10 or newer is required and must be in PATH." >&2
    exit 1
fi

INSTALL_DIR="$HOME/.local/bin"
if [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    INSTALL_DIR=/usr/local/bin
fi
mkdir -p "$INSTALL_DIR"
LAUNCHER="$INSTALL_DIR/zircon"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT=$(printf '%q' "$PROJECT_ROOT")
PYTHON=$(printf '%q' "$PYTHON")

if [ ! -f "\$PROJECT_ROOT/cli/tui/__init__.py" ]; then
    echo "[ERROR] Zircon moved after installation. Run install.sh again." >&2
    exit 1
fi

if [ \$# -eq 0 ] || [[ "\${1:-}" == -* ]]; then
    exec "\$PYTHON" "\$PROJECT_ROOT/__main__.py" "\$PWD" "\$@"
else
    exec "\$PYTHON" "\$PROJECT_ROOT/__main__.py" "\$@"
fi
EOF
chmod +x "$LAUNCHER"

PROFILE=""
case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *)
        if [ -f "$HOME/.zshrc" ]; then
            PROFILE="$HOME/.zshrc"
        elif [ -f "$HOME/.bashrc" ]; then
            PROFILE="$HOME/.bashrc"
        elif [ -f "$HOME/.bash_profile" ]; then
            PROFILE="$HOME/.bash_profile"
        fi
        if [ -n "$PROFILE" ] && ! grep -F "$INSTALL_DIR" "$PROFILE" >/dev/null 2>&1; then
            printf '\n# Added by Zircon installer\nexport PATH="$PATH:%s"\n' "$INSTALL_DIR" >> "$PROFILE"
        fi
        ;;
esac

printf -v TERMINAL_COMMAND 'cd %q && export PATH=%q:$PATH && zircon' "$WORKSPACE" "$INSTALL_DIR"
echo "Zircon installed at $LAUNCHER."
echo "Opening Zircon in a new terminal..."

if [ "$(uname -s)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
    APPLE_COMMAND=${TERMINAL_COMMAND//\\/\\\\}
    APPLE_COMMAND=${APPLE_COMMAND//\"/\\\"}
    osascript -e 'tell application "Terminal" to activate' -e "tell application \"Terminal\" to do script \"$APPLE_COMMAND\"" >/dev/null
elif command -v x-terminal-emulator >/dev/null 2>&1; then
    x-terminal-emulator -e bash -lc "$TERMINAL_COMMAND; exec bash" >/dev/null 2>&1 &
elif command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal -- bash -lc "$TERMINAL_COMMAND; exec bash" >/dev/null 2>&1 &
elif command -v konsole >/dev/null 2>&1; then
    konsole -e bash -lc "$TERMINAL_COMMAND; exec bash" >/dev/null 2>&1 &
elif command -v xterm >/dev/null 2>&1; then
    xterm -e bash -lc "$TERMINAL_COMMAND; exec bash" >/dev/null 2>&1 &
else
    echo "[WARN] No supported terminal emulator was found. Run: zircon" >&2
fi
