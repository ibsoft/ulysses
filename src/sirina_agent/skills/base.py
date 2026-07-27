from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SkillManifest:
    name: str
    description: str
    arguments_schema: dict[str, Any]
    required_permissions: list[str]
    risk_level: str
    enabled: bool = True


@dataclass
class SkillResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    confirmation_prompt: str | None = None
    confirmation_token: str | None = None


class Skill(Protocol):
    manifest: SkillManifest

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult: ...
