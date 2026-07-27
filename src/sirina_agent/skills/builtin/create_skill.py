from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from ..base import SkillManifest, SkillResult


class CreateSkillSkill:
    manifest = SkillManifest(
        name="create_skill",
        description="Scaffold a new local Ulysses skill from a user request.",
        arguments_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short snake_case skill name."},
                "request": {"type": "string", "description": "What the user wants the new skill to do."},
                "description": {"type": "string", "description": "One sentence skill description."},
                "required_permissions": {"type": "array", "items": {"type": "string"}},
                "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                "enabled": {"type": "boolean", "description": "Whether to enable the generated skill immediately."},
                "confirmed": {"type": "boolean"},
                "confirmation_text": {"type": "string"},
            },
            "required": ["name", "request"],
        },
        required_permissions=["write_skills_dir"],
        risk_level="high",
        enabled=True,
    )

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        name = _normalize_name(str(arguments["name"]))
        if not name:
            return SkillResult(False, "Skill name must contain letters or numbers.")
        request = str(arguments["request"]).strip()
        if not request:
            return SkillResult(False, "Skill request cannot be empty.")
        target_dir = (self.skills_dir / name).resolve()
        skills_root = self.skills_dir.resolve()
        if skills_root not in target_dir.parents:
            return SkillResult(False, "Refusing to write outside the configured skills directory.")
        if target_dir.exists():
            return SkillResult(False, f"Skill `{name}` already exists at {target_dir}.")

        token = hashlib.blake2b(f"{name}:{request}".encode("utf-8"), digest_size=4).hexdigest()
        if not arguments.get("confirmed") or arguments.get("confirmation_text") != token:
            return SkillResult(
                False,
                "Skill creation requires typed confirmation.",
                {"path": str(target_dir), "name": name},
                True,
                f"Create new skill `{name}` in `{target_dir}`? Confirmation token: {token}",
                token,
            )

        description = str(arguments.get("description") or _description_from_request(request))
        required_permissions = list(arguments.get("required_permissions") or [])
        risk_level = str(arguments.get("risk_level") or "medium")
        enabled = bool(arguments.get("enabled", False))
        target_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "name": name,
            "description": description,
            "arguments_schema": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            },
            "required_permissions": required_permissions,
            "risk_level": risk_level,
            "enabled": enabled,
        }
        (target_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        (target_dir / "skill.py").write_text(_skill_template(name, description, request), encoding="utf-8")
        (target_dir / "README.md").write_text(_readme_template(name, description, request, enabled), encoding="utf-8")
        return SkillResult(
            True,
            f"Created skill `{name}` at {target_dir}. Review `skill.py`, then enable it in `manifest.yaml` if needed and restart Ulysses.",
            {"path": str(target_dir), "enabled": enabled},
        )


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if normalized and not normalized[0].isalpha():
        normalized = f"skill_{normalized}"
    return normalized[:64]


def _description_from_request(request: str) -> str:
    cleaned = " ".join(request.split())
    return cleaned[:140] if cleaned else "Generated Ulysses skill."


def _skill_template(name: str, description: str, request: str) -> str:
    return f'''from __future__ import annotations

from typing import Any

from sirina_agent.skills.base import SkillManifest, SkillResult


class SkillImpl:
    manifest = SkillManifest(
        name={name!r},
        description={description!r},
        arguments_schema={{
            "type": "object",
            "properties": {{"input": {{"type": "string"}}}},
            "required": ["input"],
        }},
        required_permissions=[],
        risk_level="medium",
        enabled=False,
    )

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        user_input = str(arguments.get("input", ""))
        return SkillResult(
            False,
            "Skill `{name}` was scaffolded but its implementation still needs to be completed.",
            {{"input": user_input, "original_request": {request!r}}},
        )
'''


def _readme_template(name: str, description: str, request: str, enabled: bool) -> str:
    return f"""# {name}

{description}

Original request:

```text
{request}
```

Enabled on creation: `{enabled}`

Edit `skill.py` to implement the behavior. Keep secrets out of source code and add any permissions to `manifest.yaml`.
"""
