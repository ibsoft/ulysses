from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..base import SkillManifest, SkillResult
from ..builder import SkillBuildError, validate_generated_skill_source


class CreateSkillSkill:
    manifest = SkillManifest(
        name="create_skill",
        description="Research, generate, validate, activate, and register a complete local Ulysses skill.",
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
        activity_label="constructing skill",
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
        replacing = target_dir.exists()

        token = hashlib.blake2b(f"{name}:{request}".encode("utf-8"), digest_size=4).hexdigest()
        if not arguments.get("confirmed") or arguments.get("confirmation_text") != token:
            return SkillResult(
                False,
                "Skill creation requires typed confirmation.",
                {"path": str(target_dir), "name": name},
                True,
                f"{'Replace existing' if replacing else 'Create new'} skill `{name}` in `{target_dir}`? "
                f"Confirmation token: {token}",
                token,
            )

        description = str(arguments.get("description") or _description_from_request(request))
        required_permissions = list(arguments.get("required_permissions") or [])
        risk_level = str(arguments.get("risk_level") or "medium")
        generated_source = str(arguments.get("generated_source") or "")
        arguments_schema = arguments.get("arguments_schema") or {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
        enabled = bool(arguments.get("enabled", bool(generated_source)))
        if generated_source:
            try:
                validate_generated_skill_source(generated_source, arguments_schema)
            except SkillBuildError as exc:
                return SkillResult(False, str(exc))
        manifest = {
            "name": name,
            "description": description,
            "arguments_schema": arguments_schema,
            "required_permissions": required_permissions,
            "risk_level": risk_level,
            "enabled": enabled,
        }
        backup_dir: Path | None = None
        if replacing:
            backup_root = skills_root / ".backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_dir = backup_root / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            shutil.move(str(target_dir), str(backup_dir))
        try:
            target_dir.mkdir(parents=True, exist_ok=False)
            (target_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            (target_dir / "skill.py").write_text(generated_source or _skill_template(name, description, request), encoding="utf-8")
            (target_dir / "README.md").write_text(
                _readme_template(
                    name,
                    description,
                    request,
                    enabled,
                    str(arguments.get("research") or ""),
                    complete=bool(generated_source),
                ),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            if backup_dir is not None:
                shutil.move(str(backup_dir), str(target_dir))
            raise
        return SkillResult(
            True,
            f"{'Replaced' if replacing else 'Created'} {'complete' if generated_source else 'scaffold'} skill `{name}` at {target_dir}.",
            {"path": str(target_dir), "enabled": enabled, "replaced": replacing},
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


def _readme_template(
    name: str,
    description: str,
    request: str,
    enabled: bool,
    research: str = "",
    complete: bool = False,
) -> str:
    research_section = f"\n## Research used\n\n```text\n{research[:12000]}\n```\n" if research else ""
    implementation_note = (
        "Implementation generated and activated by Ulysses. Review source before granting additional permissions."
        if complete
        else "Edit `skill.py` to implement the behavior, then enable it in `manifest.yaml`."
    )
    return f"""# {name}

{description}

Original request:

```text
{request}
```

Enabled on creation: `{enabled}`

{implementation_note} Keep secrets out of source code and declare permissions in `manifest.yaml`.
{research_section}
"""
