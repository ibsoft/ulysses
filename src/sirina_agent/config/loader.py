from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import CommandSkillConfig, UlyssesConfig


def _set_nested(data: dict[str, Any], path: list[str], value: str) -> None:
    cursor = data
    for part in path[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[path[-1]] = value


def _env_overrides(prefix: str = "ULYSSES__") -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in os.environ.items():
        if key.startswith(prefix):
            _set_nested(data, key.removeprefix(prefix).lower().split("__"), value)
    return data


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> UlyssesConfig:
    raw: dict[str, Any] = {}
    if path:
        config_path = Path(path).expanduser()
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
    merged = _merge(raw, _env_overrides())
    _merge_supported_commands(merged)
    return UlyssesConfig.model_validate(merged)


def _merge_supported_commands(data: dict[str, Any]) -> None:
    command = data.setdefault("skills", {}).setdefault("command", {})
    configured = command.get("allowed_commands")
    if configured is None:
        return
    if not isinstance(configured, list):
        return
    supported = CommandSkillConfig().allowed_commands
    command["allowed_commands"] = list(dict.fromkeys([*configured, *supported]))
