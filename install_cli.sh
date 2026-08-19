#!/bin/bash
# =============================================================================
#  Zircon CLI Installer — macOS/Linux
#  Installs the 'zircon' command so you can run it from any terminal.
#  Usage:  ./install_cli.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR" && pwd)"

echo ""
echo "  ███████╗██╗██████╗  ██████╗ ██████╗ ███╗   ██╗"
echo "  ╚══███╔╝██║██╔══██╗██╔════╝██╔═══██╗████╗  ██║"
echo "    ███╔╝ ██║██████╔╝██║     ██║   ██║██╔██╗ ██║"
echo "   ███╔╝  ██║██╔══██╗██║     ██║   ██║██║╚██╗██║"
echo "  ███████╗██║██║  ██║╚██████╗╚██████╔╝██║ ╚████║"
echo "  ╚══════╝╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝"
echo "  Zircon v1.0 — CLI Installer"
echo ""

# ── Determine install directory ─────────────────────────────────────────────
# Prefer ~/.local/bin (already in your PATH), fall back to ~/bin, then /usr/local/bin.

INSTALL_DIR=""
for candidate in "$HOME/.local/bin" "$HOME/bin"; do
    if [ -d "$candidate" ]; then
        INSTALL_DIR="$candidate"
        break
    fi
done

if [ -z "$INSTALL_DIR" ]; then
    # Default to ~/.local/bin and create it
    INSTALL_DIR="$HOME/.local/bin"
    mkdir -p "$INSTALL_DIR"
fi

# If we can write directly to a system path, prefer it
if [ -d "/usr/local/bin" ] && [ -w "/usr/local/bin" ]; then
    INSTALL_DIR="/usr/local/bin"
fi

echo "  Installing to: $INSTALL_DIR"

# ── Create the 'zircon' launcher script ─────────────────────────────────────
LAUNCHER="$INSTALL_DIR/zircon"

cat > "$LAUNCHER" << LAUNCHER_HEADER
#!/bin/bash
# =============================================================================
#  zircon — CLI launcher for Zircon Agent
#  Run from any directory to open the Rich TUI chat in that folder.
#  Usage:  zircon [--low|--quality|--swarm] [<workspace-path>]
# =============================================================================

set -euo pipefail

# Project location baked in at install time
ZIRCON_INSTALL_ROOT="$PROJECT_ROOT"
LAUNCHER_HEADER

cat >> "$LAUNCHER" << 'LAUNCHER_EOF'

# Capture the user's original working directory BEFORE we cd anywhere
ORIGINAL_PWD="$PWD"

# Determine project root by walking up from the launcher script's own location
LAUNCHER_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

# Try common locations for the project
PROJECT_ROOT=""

# Prefer the path recorded at install time
if [ -n "${ZIRCON_INSTALL_ROOT:-}" ] && [ -f "$ZIRCON_INSTALL_ROOT/cli/__init__.py" ]; then
    PROJECT_ROOT="$ZIRCON_INSTALL_ROOT"
fi

# If the launcher is inside ~/.local/bin, sibling to the project
if [ -z "$PROJECT_ROOT" ] && [ "$(basename "$LAUNCHER_DIR")" = "bin" ]; then
    # Check common parent directories
    for base in "$HOME/Documents/projects" "$HOME" "/opt"; do
        if [ -f "$base/zirconAgent/cli/__init__.py" ]; then
            PROJECT_ROOT="$base/zirconAgent"
            break
        fi
        if [ -f "$base/zirconAgent/src/cli/__init__.py" ]; then
            PROJECT_ROOT="$base/zirconAgent/src"
            break
        fi
    done
fi

# If still not found, check if it's in the same directory as the installer
if [ -z "$PROJECT_ROOT" ] && [ -f "$LAUNCHER_DIR/../zirconAgent/cli/__init__.py" ]; then
    PROJECT_ROOT="$(cd "$LAUNCHER_DIR/../zirconAgent" && pwd)"
fi

# Last resort: search up the directory tree for cli/__init__.py
if [ -z "$PROJECT_ROOT" ]; then
    CANDIDATE="$LAUNCHER_DIR"
    for _ in $(seq 1 10); do
        if [ -f "$CANDIDATE/cli/__init__.py" ]; then
            PROJECT_ROOT="$CANDIDATE"
            break
        fi
        CANDIDATE="$(dirname "$CANDIDATE")"
    done
fi

if [ -z "$PROJECT_ROOT" ] || [ ! -f "$PROJECT_ROOT/cli/__init__.py" ]; then
    echo "[ERROR] Could not find zirconAgent project."
    echo "        Run the installer from the project directory."
    exit 1
fi

cd "$PROJECT_ROOT"

# Prefer the project's venv, fall back to system Python
PYTHON=""
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python is not installed or not in PATH."
    echo "        Install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

# Pass all arguments to the new CLI package
# If the first argument looks like a flag (starts with '-') or no args given,
# inject ORIGINAL_PWD as the workspace path so 'zircon' opens in whatever
# folder it was typed from. If the first arg is NOT a flag, it's an explicit path.
if [ $# -eq 0 ] || [[ "$1" == -* ]]; then
    exec "$PYTHON" "$PROJECT_ROOT/__main__.py" "$ORIGINAL_PWD" "$@"
else
    exec "$PYTHON" "$PROJECT_ROOT/__main__.py" "$@"
fi
LAUNCHER_EOF

chmod +x "$LAUNCHER"
echo "  Created: $LAUNCHER"

# ── Ensure install dir is in PATH ────────────────────────────────────────────
NEED_PROFILE_UPDATE=false

case ":$PATH:" in
    *":$INSTALL_DIR:"*)   ;;
    *)                    NEED_PROFILE_UPDATE=true ;;
esac

if [ "$NEED_PROFILE_UPDATE" = true ]; then
    SHELL_RC=""
    if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -f "$HOME/.bash_profile" ]; then
        SHELL_RC="$HOME/.bash_profile"
    fi

    if [ -n "$SHELL_RC" ]; then
        echo ""
        echo "  Adding $INSTALL_DIR to PATH in $SHELL_RC ..."
        echo "" >> "$SHELL_RC"
        echo "# Added by Zircon installer" >> "$SHELL_RC"
        echo "export PATH=\"\$PATH:$INSTALL_DIR\"" >> "$SHELL_RC"
        echo "  Done. Restart your terminal or run: source $SHELL_RC"
    else
        echo ""
        echo "  [WARN] Could not detect shell profile."
        echo "  Add this to your shell config:"
        echo "    export PATH=\"\$PATH:$INSTALL_DIR\""
    fi
else
    echo "  $INSTALL_DIR already in PATH."
fi

echo ""
echo "  ✅ Zircon CLI installed!"
echo ""
echo "  Usage:"
echo "    zircon                  # Open TUI in current directory"
echo "    zircon /path/to/project # Open TUI in specific directory"
echo "    zircon --low            # Low tier mode"
echo "    zircon --quality        # Quality tier with full planning"
echo "    zircon --swarm          # Swarm mode"
echo ""