from __future__ import annotations

import hashlib
from typing import Any

from ..base import SkillManifest, SkillResult


class CreateSubagentSkill:
    manifest = SkillManifest(
        name="subagent_create",
        description="Create a persistent specialist sub-agent with its own prompt, files, workspace, and task history.",
        arguments_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Stable snake_case agent name."},
                "purpose": {"type": "string"},
                "prompt": {"type": "string", "description": "Complete specialist system prompt."},
            },
            "required": ["name", "purpose", "prompt"],
        },
        required_permissions=["write_subagents"],
        risk_level="medium",
    )

    def __init__(self, manager) -> None:
        self.manager = manager

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        record = self.manager.create(str(arguments["name"]), str(arguments["purpose"]), str(arguments["prompt"]))
        return SkillResult(True, f"Persistent sub-agent `{record['name']}` created and ready.", record)


class DelegateSubagentSkill:
    manifest = SkillManifest(
        name="subagent_delegate",
        description="Assign a background job to a persistent sub-agent. Returns immediately so Ulysses can keep chatting.",
        arguments_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "task": {"type": "string"}, "context": {"type": "string"}},
            "required": ["name", "task"],
        },
        required_permissions=["invoke_llm", "write_subagents"],
        risk_level="medium",
    )

    def __init__(self, manager) -> None:
        self.manager = manager

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        job = self.manager.delegate(str(arguments["name"]), str(arguments["task"]), str(arguments.get("context") or ""))
        return SkillResult(True, f"Job `{job['id']}` assigned to `{job['agent']}` in the background.", job)


class SubagentJobsSkill:
    manifest = SkillManifest(
        name="subagent_jobs",
        description="List persistent sub-agents and inspect queued, running, completed, or failed job reports.",
        arguments_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Optional sub-agent name."}},
        },
        required_permissions=["read_subagents"],
        risk_level="low",
    )

    def __init__(self, manager) -> None:
        self.manager = manager

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        name = str(arguments.get("name") or "").strip() or None
        agents = self.manager.list_agents()
        jobs = self.manager.list_jobs(name)
        lines = ["Sub-agents:"] + [f"- {item['name']}: {item['purpose']} ({item['jobs']} jobs)" for item in agents]
        lines += ["", "Jobs:"] + [
            f"- {item['id']} [{item['status']}] -> {item['agent']}: {item['task']}" for item in jobs[:50]
        ]
        return SkillResult(True, "\n".join(lines), {"agents": agents, "jobs": jobs[:50]})


class DeleteSubagentSkill:
    manifest = SkillManifest(
        name="subagent_delete",
        description="Permanently delete an idle persistent sub-agent and all files in its isolated workspace.",
        arguments_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "confirmed": {"type": "boolean"},
                "confirmation_text": {"type": "string"},
            },
            "required": ["name"],
        },
        required_permissions=["delete_subagents"],
        risk_level="high",
    )

    def __init__(self, manager) -> None:
        self.manager = manager

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        name = str(arguments["name"]).strip().lower().replace("-", "_")
        token = hashlib.blake2b(f"delete-subagent:{name}".encode(), digest_size=4).hexdigest()
        if not arguments.get("confirmed") or arguments.get("confirmation_text") != token:
            return SkillResult(
                False,
                "Sub-agent deletion requires typed confirmation.",
                {"name": name},
                True,
                f"Permanently delete sub-agent `{name}` and all of its files? Confirmation token: {token}",
                token,
            )
        self.manager.delete(name)
        return SkillResult(True, f"Sub-agent `{name}` and its workspace were deleted.", {"name": name})
