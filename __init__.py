from .cli import main as cli_main

# TUI entry point (chat_tui) is loaded on demand to avoid module-loading
# conflicts with `python -m zirconAgent.chat_tui`.
# Use: python -m zirconAgent.chat_tui [path] [--options]
