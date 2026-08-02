from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import UlyssesConfig

PROVIDER_DEFAULTS = {
    "openai": {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "kimi": {
        "provider": "kimi",
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2.7-code",
        "api_key_env": "KIMI_API_KEY",
    },
    "ollama": {
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "api_key_env": "OLLAMA_API_KEY",
    },
    "openai_chatgpt": {
        "provider": "openai_chatgpt",
        "base_url": "",
        "model": "",
        "api_key_env": "",
    },
}


@dataclass(frozen=True)
class ProviderSetup:
    provider: str
    model: str
    base_url: str
    api_key_env: str = ""
    api_key: str = ""


def config_path_for_runtime(config_path: str | Path | None) -> Path:
    return Path(config_path or "config/ulysses.yaml").expanduser()


def env_path_for_config(config_path: Path) -> Path:
    return config_path.parent / "env"


def apply_provider_setup(config: UlyssesConfig, config_path: Path, setup: ProviderSetup) -> None:
    provider = setup.provider.strip()
    data = config.model_dump(mode="json")
    llm = data.setdefault("llm", {})
    llm["provider"] = provider
    llm["model"] = setup.model.strip()
    llm["base_url"] = setup.base_url.strip().rstrip("/")
    llm["api_key_env"] = (
        ""
        if provider == "openai_chatgpt"
        else (setup.api_key_env or default_for(provider, "api_key_env") or "OPENAI_API_KEY").strip()
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    env_updates: dict[str, str] = {}
    if setup.api_key:
        env_updates[llm["api_key_env"]] = setup.api_key
    if provider == "ollama":
        env_updates.setdefault(llm["api_key_env"], "ollama")
    if env_updates:
        update_env_file(env_path_for_config(config_path), env_updates)


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def default_for(provider: str, key: str) -> str:
    value = PROVIDER_DEFAULTS.get(provider, {}).get(key, "")
    return str(value or "")


def setup_from_provider(provider: str) -> ProviderSetup:
    defaults = PROVIDER_DEFAULTS[provider]
    return ProviderSetup(
        provider=str(defaults["provider"]),
        model=str(defaults["model"]),
        base_url=str(defaults["base_url"]),
        api_key_env=str(defaults.get("api_key_env") or ""),
    )


def provider_labels() -> list[tuple[str, str]]:
    return [
        ("openai", "OpenAI API key"),
        ("openai_chatgpt", "OpenAI-Codex"),
        ("kimi", "Kimi"),
        ("ollama", "Ollama"),
    ]


def complete_name_onboarding(config: UlyssesConfig, config_path: Path) -> None:
    """Persist that the one-time preferred-name prompt has been delivered or migrated."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("tui", {})["name_prompt_completed"] = True
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    config.tui.name_prompt_completed = True
