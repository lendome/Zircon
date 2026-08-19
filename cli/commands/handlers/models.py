"""
Models handler — list the models available from the configured provider so the
user can switch to a model that isn't already in models.yaml.

Reads base_url/api_key from the config profiles, then queries the provider's
/models endpoint (same logic as the frontend api_fetch_models backend).
"""

from __future__ import annotations

import json
import urllib.request

from ._shared import _find_config_path
from ...runtime import ParsedArgs, RuntimeContext


def _provider_endpoint() -> tuple[str, str] | None:
    """Return (base_url, api_key) for the provider, or None if not configured."""
    import yaml

    config_path = _find_config_path()
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        return None

    profiles = cfg.get("profiles") or {}
    # Prefer 'default', fall back to the first profile with a base_url.
    ordered = [profiles.get("default")] + list(profiles.values())
    for profile in ordered:
        profile = profile or {}
        base_url = (profile.get("base_url") or "").rstrip("/")
        if base_url:
            api_key = profile.get("api_key") or ""
            return base_url, api_key
    return None


def _fetch_models(base_url: str, api_key: str) -> list[str]:
    """Query the provider's /models endpoint and return sorted model ids."""
    candidates = [
        f"{base_url}/models",
        f"{base_url}/v1/models",
        f"{base_url}/api/models",
    ]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    models_set: set[str] = set()

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read())
            for item in body.get("data", []):
                mid = item.get("id", "")
                if mid:
                    models_set.add(mid)
            for item in body.get("models", []):
                mid = item.get("name", item.get("model", item.get("id", "")))
                if mid:
                    models_set.add(mid)
        except Exception:
            continue
        if models_set:
            break  # First successful endpoint wins

    return sorted(models_set)


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    endpoint = _provider_endpoint()
    if not endpoint:
        print("\033[91mNo provider configured\033[0m")
        print("  Add a profile with base_url to models.yaml to configure a provider.")
        return 0

    base_url, api_key = endpoint
    models = _fetch_models(base_url, api_key)

    if not models:
        print(f"\033[91mCould not fetch models from {base_url}\033[0m")
        print("  Check the base_url/api_key in models.yaml and your network connection.")
        return 1

    print(f"\033[92mModels available from {base_url}:\033[0m")
    for mid in models:
        print(f"  - {mid}")

    return 0
