from __future__ import annotations

import os
import re
import tempfile
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import ModelProfile, Tier, TierConfig, TIER_PRESETS

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "models.yaml"
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _load_dotenv(dotenv_path: Path | None = None) -> dict[str, str]:
    """Load a .env file, returning a dict of key-value pairs."""
    if dotenv_path is None:
        dotenv_path = Path(__file__).parent.parent / ".env"
    env_vars: dict[str, str] = {}
    if not dotenv_path.exists():
        return env_vars
    try:
        import dotenv
        return {**dotenv.dotenv_values(str(dotenv_path))}
    except ImportError:
        # Fallback: manual parse
        try:
            for line in dotenv_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env_vars[key] = value
        except Exception:
            pass
    return env_vars


def _resolve_env_vars(value: str, env: dict[str, str] | None = None) -> str:
    """Resolve ${VAR_NAME} patterns in a string using env vars and .env file.

    Priority: 1) actual os.environ  2) .env file values  3) default if no match
    """
    if env is None:
        env = _load_dotenv()

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        # 1st priority: actual process environment
        if var_name in os.environ:
            return os.environ[var_name]
        # 2nd priority: .env file values
        if var_name in env:
            return env[var_name]
        # 3rd: keep the literal string as-is (for defaults)
        return match.group(0)

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_value(val: Any, env: dict[str, str]) -> Any:
    """Recursively resolve env vars in strings within a nested structure."""
    if isinstance(val, str):
        return _resolve_env_vars(val, env)
    elif isinstance(val, dict):
        return {k: _resolve_value(v, env) for k, v in val.items()}
    elif isinstance(val, list):
        return [_resolve_value(item, env) for item in val]
    return val


@dataclass
class AgentConfig:
    max_tool_turns: int = 20
    max_plan_steps: int = 12
    subagent_max_turns: int = 10
    working_set_max_files: int = 30
    context_safety_margin: float = 0.10
    safety_margin: int = 400
    tier: Any = None  # Tier enum, defaults to BALANCED in Agent.__init__
    tier: Tier = Tier.BALANCED
    # Optional `web_search:` section from models.yaml
    # (backend: ddg|brave|tavily, api_key: "${BRAVE_API_KEY}")
    web_search: dict[str, Any] = field(default_factory=dict)


@dataclass
class RouterConfig:
    default_role: str = "default"
    failover_enabled: bool = True
    streaming: bool = True
    max_retries: int = 4
    retry_base_delay: float = 1.0
    retry_max_delay: float = 32.0
    rate_limit_delay: float = 0.5
    max_concurrent: int = 3
    fast_mode: bool = False  # route to highest-throughput providers (nitro)
    role_priority: dict[str, list[str]] = field(default_factory=dict)
    profiles: list[ModelProfile] = field(default_factory=list)


def load_config(config_path: Path | str | None = None) -> tuple[RouterConfig, AgentConfig]:
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH

    # Load .env file for env var resolution
    env = _load_dotenv()

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Resolve ${VAR} patterns in the entire YAML
    resolved = _resolve_value(raw, env)

    profiles = []
    for name, pdata in resolved.get("profiles", {}).items():
        profiles.append(ModelProfile(
            name=name,
            base_url=pdata["base_url"],
            api_key=pdata.get("api_key", ""),
            model=pdata["model"],
            max_tokens=pdata.get("max_tokens", 32768),
            context_window=pdata.get("context_window", 32000),
            roles=pdata.get("roles", []),
            timeout=pdata.get("timeout", 120.0),
            reasoning_effort=pdata.get("reasoning_effort"),
            reasoning_enabled=pdata.get("reasoning_enabled"),
            supports_vision=pdata.get("supports_vision"),
        ))

    router_data = resolved.get("router", {})
    role_priority = {}
    for role, candidates in router_data.get("role_priority", {}).items():
        role_priority[role] = candidates

    router_cfg = RouterConfig(
        default_role=router_data.get("default_role", "default"),
        failover_enabled=router_data.get("failover_enabled", True),
        streaming=router_data.get("streaming", True),
        max_retries=router_data.get("max_retries", 4),
        retry_base_delay=router_data.get("retry_base_delay", 1.0),
        retry_max_delay=router_data.get("retry_max_delay", 32.0),
        rate_limit_delay=router_data.get("rate_limit_delay", 0.5),
        max_concurrent=router_data.get("max_concurrent", 3),
        fast_mode=router_data.get("fast_mode", False),
        role_priority=role_priority,
        profiles=profiles,
    )

    agent_cfg = AgentConfig(web_search=resolved.get("web_search", {}) or {})
    return router_cfg, agent_cfg


def save_config(config: dict, config_path: Path | str | None = None) -> None:
    """Save config back to models.yaml, masking env var references so literal keys
    are NOT written back to the file. Instead, values that match env vars are
    replaced with their ${VAR} reference."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    env = _load_dotenv()

    # Create reverse lookup: value -> env_var_name
    value_to_var: dict[str, str] = {}
    # Include actual os.environ too
    for var_name in list(env.keys()) + list(os.environ.keys()):
        if var_name.startswith("OPENROUTER_API_KEY"):
            value_to_var[os.environ.get(var_name, env.get(var_name, ""))] = var_name
        if var_name.startswith("ANTHROPIC_API_KEY"):
            value_to_var[os.environ.get(var_name, env.get(var_name, ""))] = var_name
        if var_name.startswith("OPENAI_API_KEY"):
            value_to_var[os.environ.get(var_name, env.get(var_name, ""))] = var_name

    # Recurse through config and replace known key values with ${VAR}
    def _mask_keys(d: Any) -> Any:
        if isinstance(d, dict):
            return {k: _mask_keys(v) for k, v in d.items()}
        elif isinstance(d, str):
            for val, var_name in value_to_var.items():
                if val and d == val:
                    return f"${{{var_name}}}"
            return d
        return d

    masked = _mask_keys(config)

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        yaml.dump(masked, handle, default_flow_style=False, sort_keys=False)
        temp_path = Path(handle.name)
    temp_path.replace(path)
