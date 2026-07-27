from sirina_agent.config.loader import load_config


def test_load_config_env_override(tmp_path, monkeypatch):
    config_path = tmp_path / "ulysses.yaml"
    config_path.write_text("agent_name: Ulysses\nllm:\n  provider: mock\n", encoding="utf-8")
    monkeypatch.setenv("ULYSSES__MEMORY__TOP_K", "3")
    cfg = load_config(config_path)
    assert cfg.agent_name == "Ulysses"
    assert cfg.llm.provider == "mock"
    assert cfg.memory.top_k == 3
