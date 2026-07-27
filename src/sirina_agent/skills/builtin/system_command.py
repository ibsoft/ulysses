from __future__ import annotations

import hashlib
from typing import Any

from ...security.commands import CommandRunner
from ..base import SkillManifest, SkillResult


class SystemCommandSkill:
    manifest = SkillManifest(
        name="system_command",
        description="Run allowlisted local system commands after confirmation.",
        arguments_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "confirmed": {"type": "boolean"},
                "confirmation_text": {"type": "string"},
                "sudo_password": {"type": "string"},
            },
            "required": ["command"],
        },
        required_permissions=["local_process"],
        risk_level="high",
        enabled=True,
    )

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def run(self, arguments: dict[str, Any], context: dict[str, Any]) -> SkillResult:
        command = str(arguments["command"])
        decision = self.runner.policy.evaluate(command)
        if not decision.allowed:
            return SkillResult(False, decision.reason, {"decision": decision.__dict__})
        token = hashlib.blake2b(" ".join(decision.argv).encode("utf-8"), digest_size=4).hexdigest()
        confirmed = bool(arguments.get("confirmed"))
        typed_ok = str(arguments.get("confirmation_text", "")) == token
        if decision.requires_confirmation and not confirmed:
            return SkillResult(
                False,
                "Command requires confirmation.",
                {"decision": decision.__dict__},
                True,
                f"Run `{ ' '.join(decision.argv) }`? Confirmation token: {token}",
                token,
            )
        if decision.requires_typed_confirmation and not typed_ok:
            return SkillResult(False, f"High-risk command requires typed confirmation token: {token}", {"decision": decision.__dict__}, True, confirmation_token=token)
        sudo_password = arguments.get("sudo_password")
        if decision.sudo_password_required and not sudo_password:
            return SkillResult(
                False,
                "Sudo password required.",
                {"decision": decision.__dict__, "sudo_password_required": True},
                True,
                "Enter sudo password in the TUI to execute this command.",
                token,
            )
        result = self.runner.run(decision.argv, sudo_password=str(sudo_password) if sudo_password else None)
        return SkillResult(result["returncode"] == 0, result["stdout"] or result["stderr"], result)
