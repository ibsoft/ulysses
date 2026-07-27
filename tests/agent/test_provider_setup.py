import os

from sirina_agent.config.loader import load_config
from sirina_agent.config.models import LLMConfig, UlyssesConfig
from sirina_agent.config.provider_setup import (
    ProviderSetup,
    apply_provider_setup,
    env_path_for_config,
    load_env_file,
    setup_from_provider,
)
from sirina_agent.llm.providers import OpenAICompatibleProvider, build_provider


def test_kimi_provider_setup_writes_yaml_and_env(tmp_path):
    config_path = tmp_path / "ulysses.yaml"
    config = UlyssesConfig()
    setup = ProviderSetup(
        provider="kimi",
        model="kimi-k2-0711-preview",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="KIMI_API_KEY",
        api_key="secret",
    )

    apply_provider_setup(config, config_path, setup)
    loaded = load_config(config_path)

    assert loaded.llm.provider == "kimi"
    assert loaded.llm.model == "kimi-k2-0711-preview"
    assert loaded.llm.base_url == "https://api.moonshot.ai/v1"
    assert loaded.llm.api_key_env == "KIMI_API_KEY"
    assert "KIMI_API_KEY=secret" in env_path_for_config(config_path).read_text(encoding="utf-8")


def test_load_env_file_exports_saved_provider_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_path = tmp_path / "env"
    env_path.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")

    load_env_file(env_path)

    assert os.environ["OPENAI_API_KEY"] == "secret"


def test_ollama_provider_uses_openai_compatible_local_endpoint(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    config = LLMConfig(provider="ollama", model="llama3.1", base_url="http://localhost:11434/v1", api_key_env="OLLAMA_API_KEY")

    provider = build_provider(config)

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.model == "llama3.1"
    assert provider.api_key == "ollama"


def test_provider_defaults_include_openai_kimi_ollama_and_oauth():
    assert setup_from_provider("openai").api_key_env == "OPENAI_API_KEY"
    assert setup_from_provider("kimi").api_key_env == "KIMI_API_KEY"
    assert setup_from_provider("ollama").base_url == "http://localhost:11434/v1"
    assert setup_from_provider("oauth_compatible").oauth_token_env == "ULYSSES_PROVIDER_TOKEN"
