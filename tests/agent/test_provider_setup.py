import os

import httpx
import pytest

from sirina_agent.config.loader import load_config
from sirina_agent.config.models import LLMConfig, UlyssesConfig
from sirina_agent.config.provider_setup import (
    ProviderSetup,
    apply_provider_setup,
    complete_name_onboarding,
    env_path_for_config,
    load_env_file,
    provider_labels,
    setup_from_provider,
)
from sirina_agent.llm.providers import (
    CodexProvider,
    LLMProviderError,
    OpenAICompatibleProvider,
    UnconfiguredProvider,
    build_provider,
)


def test_provider_setup_uses_openai_codex_label():
    assert ("openai_chatgpt", "OpenAI-Codex") in provider_labels()


def test_kimi_provider_uses_kimi_k27_code_by_default():
    assert setup_from_provider("kimi").model == "kimi-k2.7-code"


def test_name_prompt_completion_is_persisted(tmp_path):
    config_path = tmp_path / "ulysses.yaml"
    config_path.write_text("tui:\n  theme: ulysses_dark\n", encoding="utf-8")
    config = load_config(config_path)

    assert not config.tui.name_prompt_completed
    complete_name_onboarding(config, config_path)

    assert config.tui.name_prompt_completed
    assert load_config(config_path).tui.name_prompt_completed


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


def test_provider_defaults_include_openai_browser_kimi_and_ollama():
    assert setup_from_provider("openai").api_key_env == "OPENAI_API_KEY"
    assert setup_from_provider("openai_chatgpt").base_url == ""
    assert setup_from_provider("openai_chatgpt").model == ""
    assert setup_from_provider("kimi").api_key_env == "KIMI_API_KEY"
    assert setup_from_provider("ollama").base_url == "http://localhost:11434/v1"


def test_openai_browser_setup_does_not_write_a_secret(tmp_path):
    config_path = tmp_path / "ulysses.yaml"
    config = UlyssesConfig()

    setup = setup_from_provider("openai_chatgpt")
    setup = ProviderSetup(setup.provider, "gpt-5.3-codex", setup.base_url)
    apply_provider_setup(config, config_path, setup)

    loaded = load_config(config_path)
    assert loaded.llm.provider == "openai_chatgpt"
    assert loaded.llm.model == "gpt-5.3-codex"
    assert loaded.llm.base_url == ""
    assert loaded.llm.api_key_env == ""
    assert not env_path_for_config(config_path).exists()


def test_openai_browser_provider_uses_codex_backend():
    provider = build_provider(LLMConfig(provider="openai_chatgpt", model="gpt-5.3-codex"))

    assert isinstance(provider, CodexProvider)
    assert provider.model == "gpt-5.3-codex"


def test_unconfigured_provider_keeps_first_run_responsive():
    provider = UnconfiguredProvider("OPENAI_API_KEY is missing")

    response = provider.complete([{"role": "user", "content": "hello"}])

    assert not provider.configured
    assert "Press F7" in response["choices"][0]["message"]["content"]
    assert "OPENAI_API_KEY" not in response["choices"][0]["message"]["content"]


def test_required_tool_request_excludes_unrelated_invalid_schemas(monkeypatch):
    provider = OpenAICompatibleProvider("https://api.example", "model", "secret")
    captured = {}

    def fake_post(payload, allow_tool_fallback=False):
        captured.update(payload)
        captured["allow_tool_fallback"] = allow_tool_fallback
        return {"choices": []}

    monkeypatch.setattr(provider, "_post", fake_post)
    tools = [
        {
            "type": "function",
            "function": {"name": "internet_search", "parameters": {"oneOf": [{"type": "object"}]}},
        },
        {
            "type": "function",
            "function": {"name": "system_command", "parameters": {"type": "object"}},
        },
    ]

    provider.complete_with_required_tool([{"role": "user", "content": "do it"}], tools, "system_command")

    assert [tool["function"]["name"] for tool in captured["tools"]] == ["system_command"]
    assert captured["tool_choice"]["function"]["name"] == "system_command"
    assert captured["allow_tool_fallback"] is False


def test_required_tool_request_rejects_missing_tool():
    provider = OpenAICompatibleProvider("https://api.example", "model", "secret")

    with pytest.raises(LLMProviderError, match="Required tool is not available"):
        provider.complete_with_required_tool([], [], "system_command")


def test_brief_completion_caps_output_and_uses_short_timeout(monkeypatch):
    provider = OpenAICompatibleProvider("https://api.example", "model", "secret", timeout_seconds=60)
    captured = {}

    def fake_post(payload, allow_tool_fallback=False, timeout_seconds=None):
        captured.update(payload)
        captured["timeout_seconds"] = timeout_seconds
        return {"choices": [{"message": {"content": "Ready."}}]}

    monkeypatch.setattr(provider, "_post", fake_post)

    provider.complete_brief([{"role": "user", "content": "Greet me."}], max_tokens=64, timeout_seconds=10)

    assert captured["max_tokens"] == 64
    assert captured["timeout_seconds"] == 10


def test_brief_required_tool_call_uses_network_planning_timeout(monkeypatch):
    provider = OpenAICompatibleProvider("https://api.example", "model", "secret", timeout_seconds=60)
    captured = {}

    def fake_post(payload, allow_tool_fallback=False, timeout_seconds=None):
        captured.update(payload)
        captured["timeout_seconds"] = timeout_seconds
        return {"choices": [{"message": {"tool_calls": []}}]}

    monkeypatch.setattr(provider, "_post", fake_post)
    tool = {"type": "function", "function": {"name": "system_command", "parameters": {"type": "object"}}}

    provider.complete_with_required_tool_brief(
        [{"role": "user", "content": "scan example.com"}],
        [tool],
        "system_command",
        timeout_seconds=15,
    )

    assert captured["tool_choice"]["function"]["name"] == "system_command"
    assert captured["timeout_seconds"] == 15


def test_required_tool_adapts_when_endpoint_rejects_forced_tool_choice(monkeypatch):
    provider = OpenAICompatibleProvider("https://api.example", "any-model", "secret")
    payloads = []

    def fake_http_post(url, **kwargs):
        payloads.append(kwargs["json"])
        request = httpx.Request("POST", url)
        if len(payloads) == 1:
            return httpx.Response(
                400,
                request=request,
                json={"error": {"message": "forced tool selection is incompatible with current mode"}},
            )
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"tool_calls": []}}]},
        )

    monkeypatch.setattr("sirina_agent.llm.providers.httpx.post", fake_http_post)
    tool = {"type": "function", "function": {"name": "dynamic_tool", "parameters": {"type": "object"}}}

    provider.complete_with_required_tool([{"role": "user", "content": "perform task"}], [tool], "dynamic_tool")

    assert payloads[0]["tool_choice"]["function"]["name"] == "dynamic_tool"
    assert "tool_choice" not in payloads[1]
    assert payloads[1]["tools"] == [tool]
    assert "dynamic_tool" in payloads[1]["messages"][-1]["content"]
