from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, init=False)
class SkillManifest:
    name: str
    description: str
    arguments_schema: dict[str, Any]
    required_permissions: list[str]
    risk_level: str
    enabled: bool = True
    activity_label: str = "working"

    def __init__(
        self,
        name: str,
        description: str,
        arguments_schema: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        risk_level: str = "low",
        enabled: bool = True,
        activity_label: str = "working",
        *,
        parameters_schema: dict[str, Any] | None = None,
    ) -> None:
        schema = arguments_schema if arguments_schema is not None else parameters_schema
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "arguments_schema", schema or {"type": "object", "properties": {}})
        object.__setattr__(self, "required_permissions", required_permissions or [])
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "activity_label", activity_label)


@dataclass(init=False)
class SkillResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    confirmation_prompt: str | None = None
    confirmation_token: str | None = None

    def __init__(
        self,
        ok: bool | None = None,
        content: str | None = None,
        data: dict[str, Any] | None = None,
        requires_confirmation: bool = False,
        confirmation_prompt: str | None = None,
        confirmation_token: str | None = None,
        *,
        success: bool | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        self.ok = bool(ok if ok is not None else success)
        if content is None:
            if error is not None:
                content = error
            elif isinstance(result, dict) and result.get("summary"):
                content = str(result["summary"])
            elif result is not None:
                content = str(result)
            else:
                content = ""
        self.content = content
        self.data = data if data is not None else (result if isinstance(result, dict) else {})
        if error is not None:
            self.data.setdefault("error", error)
        self.requires_confirmation = requires_confirmation
        self.confirmation_prompt = confirmation_prompt
        self.confirmation_token = confirmation_token


class Skill(Protocol):
    manifest: SkillManifest

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult: ...
