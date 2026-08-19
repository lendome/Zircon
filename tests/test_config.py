from pathlib import Path

from zirconAgent.core.config import load_config, RouterConfig, AgentConfig


def test_load_config_default():
    config_path = Path(__file__).parent.parent / "models.yaml"
    router_cfg, agent_cfg = load_config(config_path)
    assert isinstance(router_cfg, RouterConfig)
    assert isinstance(agent_cfg, AgentConfig)


def test_load_config_profiles():
    config_path = Path(__file__).parent.parent / "models.yaml"
    router_cfg, _ = load_config(config_path)
    assert len(router_cfg.profiles) >= 4
    names = [p.name for p in router_cfg.profiles]
    assert "default" in names
    assert "frontier" in names
    assert "fast_apply" in names
    assert "small" in names


def test_load_config_role_priority():
    config_path = Path(__file__).parent.parent / "models.yaml"
    router_cfg, _ = load_config(config_path)
    assert "architect" in router_cfg.role_priority
    assert router_cfg.role_priority["architect"] == ["frontier", "default"]


def test_load_config_router_settings():
    config_path = Path(__file__).parent.parent / "models.yaml"
    router_cfg, _ = load_config(config_path)
    assert router_cfg.default_role == "default"
    assert router_cfg.failover_enabled is True
    assert router_cfg.max_retries == 4
    assert router_cfg.streaming is True


def test_profile_credentials():
    config_path = Path(__file__).parent.parent / "models.yaml"
    router_cfg, _ = load_config(config_path)
    for p in router_cfg.profiles:
        assert p.base_url.startswith("https://")
        assert p.api_key.startswith("sk-or-v1-")
        assert p.model
        assert p.context_window > 0


def test_agent_config_defaults():
    config_path = Path(__file__).parent.parent / "models.yaml"
    _, agent_cfg = load_config(config_path)
    assert agent_cfg.max_tool_turns == 20
    assert agent_cfg.working_set_max_files == 30
