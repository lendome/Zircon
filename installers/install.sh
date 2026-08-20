#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE="${ZIRCON_INSTALL_WORKSPACE:-$PWD}"

case "$(uname -s 2>/dev/null || true)" in
    Darwin) OS_NAME="macOS" ;;
    Linux) OS_NAME="Linux" ;;
    MINGW*|MSYS*|CYGWIN*)
        exec cmd.exe /c "$(cygpath -w "$SCRIPT_DIR/install.bat")"
        ;;
    *)
        echo "[ERROR] Unsupported operating system: $(uname -s 2>/dev/null || echo unknown)" >&2
        exit 1
        ;;
esac
echo "Detected $OS_NAME."

if [ ! -f "$PROJECT_ROOT/cli/tui/__init__.py" ]; then
    echo "[ERROR] Zircon TUI was not found under $PROJECT_ROOT/cli/tui." >&2
    exit 1
fi

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Python 3.10 or newer was not found. Downloading Python 3.12..."
    UV_BIN=""
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
    else
        if command -v curl >/dev/null 2>&1; then
            curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
        else
            echo "[ERROR] Python cannot be downloaded because neither curl nor wget is installed." >&2
            exit 1
        fi
        for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
            if [ -x "$candidate" ]; then
                UV_BIN="$candidate"
                break
            fi
        done
    fi
    if [ -z "$UV_BIN" ]; then
        echo "[ERROR] The Python downloader could not be installed." >&2
        exit 1
    fi
    "$UV_BIN" python install 3.12
    PYTHON="$("$UV_BIN" python find 3.12)"
fi

echo "Using Python: $PYTHON"
if [ -x "$PROJECT_ROOT/.venv/bin/python" ] && ! "$PROJECT_ROOT/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    echo "Updating the existing virtual environment..."
    "$PYTHON" -m venv --upgrade "$PROJECT_ROOT/.venv"
fi
if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$PROJECT_ROOT/.venv"
fi
PYTHON="$PROJECT_ROOT/.venv/bin/python"

echo "Installing Zircon dependencies..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel
"$PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt"

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
