from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

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
        "model": "kimi-k2-0711-preview",
        "api_key_env": "KIMI_API_KEY",
    },
    "ollama": {
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "api_key_env": "OLLAMA_API_KEY",
    },
    "oauth_compatible": {
        "provider": "oauth_compatible",
        "base_url": "https://provider.example/v1",
        "model": "model-name",
        "oauth_token_env": "ULYSSES_PROVIDER_TOKEN",
    },
}


@dataclass(frozen=True)
class ProviderSetup:
    provider: str
    model: str
    base_url: str
    api_key_env: str = ""
    api_key: str = ""
    oauth_token_env: str = ""
    oauth_token: str = ""


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
    llm["api_key_env"] = (setup.api_key_env or default_for(provider, "api_key_env") or "OPENAI_API_KEY").strip()
    llm["oauth_token_env"] = (setup.oauth_token_env or default_for(provider, "oauth_token_env") or "").strip() or None
    llm["oauth_keyring_service"] = None
    llm["oauth_keyring_username"] = None

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    env_updates: dict[str, str] = {}
    if setup.api_key:
        env_updates[llm["api_key_env"]] = setup.api_key
    if setup.oauth_token and llm["oauth_token_env"]:
        env_updates[llm["oauth_token_env"]] = setup.oauth_token
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
        oauth_token_env=str(defaults.get("oauth_token_env") or ""),
    )


def provider_labels() -> list[tuple[str, str]]:
    return [
        ("openai", "OpenAI"),
        ("kimi", "Kimi"),
        ("ollama", "Ollama"),
        ("oauth_compatible", "OAuth"),
    ]
