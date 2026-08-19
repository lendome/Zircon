"""
Help handler — print the full command tree.
"""

from __future__ import annotations

from ...runtime import ParsedArgs, RuntimeContext
from ...spec import build_root_spec


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    root = build_root_spec()
    print(f"\033[1m{root.name}\033[0m — {root.description}\n")
    print("Commands:")
    for path, spec in root.flatten():
        if spec is root:
            continue
        print(f"  {path:<30} {spec.description}")
    print()
    print("Flags:")
    print("  --low                Low/fast tier")
    print("  --quality            Quality tier")
    print("  --plan-mode          Enable planning")
    print("  --swarm              Swarm mode")
    print("  --verbose, -v        Verbose logging")
    print()
    print("Switch tiers at runtime:")
    print("  zircon tier fast         Low tier (cheap, fast)")
    print("  zircon tier balanced     Balanced tier (default)")
    print("  zircon tier quality      Quality tier (full planning)")
    return 0
