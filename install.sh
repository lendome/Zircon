#!/usr/bin/env bash
set -euo pipefail

export ZIRCON_INSTALL_WORKSPACE="$PWD"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$(uname -s 2>/dev/null || true)" in
    MINGW*|MSYS*|CYGWIN*)
        exec cmd.exe /c "$(cygpath -w "$SCRIPT_DIR/installers/install.bat")"
        ;;
esac

exec bash "$SCRIPT_DIR/installers/install.sh"
