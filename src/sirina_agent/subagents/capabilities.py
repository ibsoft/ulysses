from __future__ import annotations

import logging
from typing import Any, ClassVar


class SubagentCapabilityError(RuntimeError):
    pass


class SubagentSkillBroker:
    WORKSPACE_TOOLS: ClassVar[frozenset[str]] = frozenset({"workspace_list", "workspace_read", "workspace_write"})

    def __init__(self, config, registry=None, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.registry = registry
        self.logger = logger or logging.getLogger(__name__)

    def validate_grants(self, names: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
        for name in normalized:
            self._resolve(name)
        return normalized

    def is_delegable(self, name: str) -> bool:
        try:
            self._resolve(name)
        except SubagentCapabilityError:
            return False
        return True

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        schemas = []
        for name in self.validate_grants(names):
            skill = self._resolve(name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": skill.manifest.name,
                        "description": (f"Capability delegated by Ulysses for this job: {skill.manifest.description}"),
                        "parameters": skill.manifest.arguments_schema,
                    },
                }
            )
        return schemas

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        agent: str,
        job_id: str,
        granted_skills: list[str],
    ) -> tuple[str, dict[str, Any]]:
        if name not in granted_skills:
            raise SubagentCapabilityError(f"Skill `{name}` was not granted for this job.")
        skill = self._resolve(name)
        self.logger.info(
            "Sub-agent skill call",
            extra={
                "extra": {
                    "agent": agent,
                    "job_id": job_id,
                    "skill": name,
                    "argument_keys": sorted(arguments),
                }
            },
        )
        result = skill.run(arguments, {"actor": "subagent", "agent": agent, "job_id": job_id})
        if result.requires_confirmation:
            raise SubagentCapabilityError(
                f"Skill `{name}` requires supervisor confirmation and cannot be self-approved by a sub-agent."
            )
        content = str(result.content or "")
        limit = int(self.config.max_skill_output_chars)
        truncated = len(content) > limit
        if truncated:
            marker = "\n[Skill output truncated by sub-agent policy.]"
            content = content[: max(0, limit - len(marker))] + marker
        metadata = {
            "skill": name,
            "ok": bool(result.ok),
            "output_chars": len(content),
            "truncated": truncated,
        }
        if not result.ok:
            return f"Delegated skill `{name}` failed: {content}", metadata
        return content or f"Delegated skill `{name}` completed without text output.", metadata

    def _resolve(self, name: str):
        if name in self.WORKSPACE_TOOLS:
            raise SubagentCapabilityError(f"`{name}` is reserved for the confined workspace.")
        if name not in self.config.delegable_skills:
            raise SubagentCapabilityError(f"Skill `{name}` is not globally delegable.")
        if name in self.config.denied_skills or name.startswith("subagent_"):
            raise SubagentCapabilityError(f"Skill `{name}` is supervisor-only.")
        if name.startswith("mcp__") and not self.config.allow_mcp:
            raise SubagentCapabilityError("MCP skills are not delegable to sub-agents.")
        if self.registry is None:
            raise SubagentCapabilityError(f"Skill `{name}` is unavailable to the sub-agent broker.")
        try:
            skill = self.registry.get(name)
        except KeyError as exc:
            raise SubagentCapabilityError(f"Skill `{name}` is not registered.") from exc
        manifest = skill.manifest
        if not manifest.enabled:
            raise SubagentCapabilityError(f"Skill `{name}` is disabled.")
        if manifest.risk_level not in self.config.allowed_risk_levels:
            raise SubagentCapabilityError(f"Skill `{name}` risk level `{manifest.risk_level}` is not delegable.")
        return skill
