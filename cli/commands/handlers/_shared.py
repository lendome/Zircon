"""
Shared helpers for command handlers — agent creation, workspace setup.

Keeps handler modules thin: they call these helpers rather than duplicating
agent init logic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ...runtime import ParsedArgs, RuntimeContext


def resolve_workspace(args: ParsedArgs, ctx: RuntimeContext) -> str:
    """Determine the workspace path from args or context."""
    if args.positional:
        return str(Path(args.positional[0]).resolve())
    return str(Path(ctx.workspace).resolve())


def resolve_tier(args: ParsedArgs) -> Any:
    """Determine the execution tier from flags."""
    from zirconAgent.core.types import Tier

    if args.get("low"):
        return Tier.LOW
    if args.get("quality"):
        return Tier.QUALITY
    return Tier.BALANCED


def create_agent(
    repo_path: str,
    tier: Any | None = None,
    swarm_mode: bool = False,
    plan_mode: bool = False,
    verbose: bool = False,
    fast_mode: bool = False,
) -> Any:
    """Create an Agent instance with standard setup."""
    from zirconAgent.core.agent import Agent
    from zirconAgent.core.constants import ensure_zircon_dir
    from zirconAgent.core.logging_config import setup_logging

    if not Path(repo_path).is_dir():
        print(f"Error: directory not found: {repo_path}", file=sys.stderr)
        sys.exit(1)

    ensure_zircon_dir(repo_path)
    setup_logging(repo_path, console=verbose)

    config_path = str(_find_config_path())
    agent = Agent(
        repo_path=repo_path,
        config_path=config_path,
        tier=tier,
        swarm_mode=swarm_mode,
        plan_mode=plan_mode,
    )
    if fast_mode and hasattr(getattr(agent, "router", None), "set_fast_mode"):
        agent.router.set_fast_mode(True)
    return agent


def _find_config_path() -> Path:
    """Find models.yaml relative to the package root."""
    here = Path(__file__).resolve()  # .../zirconAgent/cli/commands/handlers/_shared.py
    # Walk up: handlers -> commands -> cli -> zirconAgent (models.yaml lives here)
    for level in range(2, 6):
        candidate = here.parents[level]
        if (candidate / "models.yaml").exists():
            return candidate / "models.yaml"
    return here.parents[3] / "models.yaml"
