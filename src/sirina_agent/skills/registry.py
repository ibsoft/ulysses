from __future__ import annotations

from pathlib import Path
import importlib.util

import yaml

from .base import Skill, SkillManifest
from .builtin.create_skill import CreateSkillSkill
from .builtin.duckduckgo_search import DuckDuckGoSearchSkill
from .builtin.system_command import SystemCommandSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.manifest.name] = skill

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def enabled(self) -> list[Skill]:
        return [skill for skill in self._skills.values() if skill.manifest.enabled]

    def manifests(self) -> list[SkillManifest]:
        return [skill.manifest for skill in self._skills.values()]

    def discover_manifests(self, skills_dir: Path) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        if not skills_dir.exists():
            return manifests
        for manifest_path in skills_dir.glob("*/manifest.yaml"):
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifests.append(SkillManifest(**data))
        return manifests

    def load_external(self, skills_dir: Path) -> list[str]:
        loaded: list[str] = []
        if not skills_dir.exists():
            return loaded
        for skill_path in sorted(skills_dir.glob("*/skill.py")):
            module_name = f"ulysses_external_skill_{skill_path.parent.name}"
            spec = importlib.util.spec_from_file_location(module_name, skill_path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            skill_cls = getattr(module, "SkillImpl", None)
            if skill_cls is None:
                continue
            skill = skill_cls()
            self.register(skill)
            loaded.append(skill.manifest.name)
        return loaded


def default_registry(command_skill: SystemCommandSkill, skills_dir: Path, include_search: bool = True) -> SkillRegistry:
    registry = SkillRegistry()
    if include_search:
        registry.register(DuckDuckGoSearchSkill())
    registry.register(CreateSkillSkill(skills_dir))
    registry.register(command_skill)
    registry.load_external(skills_dir)
    return registry
