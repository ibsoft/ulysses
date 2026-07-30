from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from sirina_agent.config.models import UlyssesConfig
from sirina_agent.config.provider_setup import env_path_for_config, update_env_file


@dataclass(frozen=True)
class TelegramSetup:
    enabled: bool
    token: str = ""


def apply_telegram_setup(config: UlyssesConfig, config_path: Path, setup: TelegramSetup) -> None:
    data = config.model_dump(mode="json")
    telegram = data.setdefault("connectors", {}).setdefault("telegram", {})
    telegram["enabled"] = setup.enabled
    token_env = str(telegram.get("token_env") or "TELEGRAM_BOT_TOKEN")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if setup.token:
        update_env_file(env_path_for_config(config_path), {token_env: setup.token.strip()})
