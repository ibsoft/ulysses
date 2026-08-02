from __future__ import annotations

from pathlib import Path

import yaml


def persist_godmode(config_path: Path, enabled: bool) -> None:
    """Persist only the Godmode setting while preserving all other YAML values."""
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    skills = data.setdefault("skills", {})
    command = skills.setdefault("command", {})
    command["godmode"] = enabled
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
