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
        self._external_manifests: dict[str, SkillManifest] = {}
        self._load_errors: dict[str, str] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.manifest.name] = skill

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def enabled(self) -> list[Skill]:
        return [skill for skill in self._skills.values() if skill.manifest.enabled]

    def manifests(self) -> list[SkillManifest]:
        return [skill.manifest for skill in self._skills.values()]

    def load_failures(self) -> list[tuple[str, SkillManifest | None, str]]:
        return [
            (name, self._external_manifests.get(name), error)
            for name, error in sorted(self._load_errors.items())
        ]

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
        self._external_manifests.clear()
        self._load_errors.clear()
        if not skills_dir.exists():
            return loaded
        for skill_path in sorted(skills_dir.glob("*/skill.py")):
            skill_dir = skill_path.parent
            name = skill_dir.name
            try:
                loaded.append(self.load_external_skill(skill_dir))
            except Exception as exc:
                manifest = self._external_manifests.get(name)
                self._load_errors[manifest.name if manifest else name] = str(exc)
                continue
        return loaded

    def load_external_skill(self, skill_dir: Path) -> str:
        skill_path = skill_dir / "skill.py"
        manifest_path = skill_dir / "manifest.yaml"
        external_manifest: SkillManifest | None = None
        if manifest_path.exists():
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            external_manifest = SkillManifest(**data)
            self._external_manifests[external_manifest.name] = external_manifest
        module_name = f"ulysses_external_skill_{skill_dir.name}"
        spec = importlib.util.spec_from_file_location(module_name, skill_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load skill module: {skill_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        skill_cls = getattr(module, "SkillImpl", None)
        if skill_cls is None:
            raise RuntimeError(f"SkillImpl is missing from: {skill_path}")
        skill = skill_cls()
        if external_manifest is not None:
            skill.manifest = external_manifest
        self.register(skill)
        self._load_errors.pop(skill.manifest.name, None)
        return skill.manifest.name


def default_registry(command_skill: SystemCommandSkill, skills_dir: Path, include_search: bool = True) -> SkillRegistry:
    registry = SkillRegistry()
    if include_search:
        registry.register(DuckDuckGoSearchSkill())
    registry.register(CreateSkillSkill(skills_dir))
    registry.register(command_skill)
    registry.load_external(skills_dir)
    return registry
