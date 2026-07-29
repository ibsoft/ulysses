from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


HIGH_RISK_TOKENS = {
    "aircrack-ng",
    "rm",
    "rmdir",
    "dd",
    "mkfs",
    "sudo",
    "su",
    "chmod",
    "chown",
    "pip",
    "pip3",
    "pipx",
    "apt",
    "apt-get",
    "dpkg",
    "systemctl",
    "service",
    "ssh",
    "scp",
    "crackmapexec",
    "hydra",
    "john",
    "hashcat",
    "masscan",
    "medusa",
    "msfconsole",
    "msfvenom",
    "netexec",
    "nuclei",
    "patator",
    "sqlmap",
    "wpscan",
}


@dataclass(frozen=True)
class CommandDecision:
    allowed: bool
    argv: list[str]
    reason: str
    high_risk: bool = False
    requires_confirmation: bool = True
    requires_typed_confirmation: bool = False
    sudo_password_required: bool = False


class CommandPolicy:
    def __init__(
        self,
        allowed_commands: list[str],
        denied_commands: list[str],
        working_directory: Path,
        env_allowlist: list[str],
        require_confirmation: bool = True,
        require_typed_confirmation_for_high_risk: bool = True,
        bypass_confirmation_for_allowed_commands: bool = False,
        godmode: bool = False,
    ) -> None:
        self.allowed = set(allowed_commands)
        self.denied = set(denied_commands)
        self.working_directory = working_directory.resolve()
        self.env_allowlist = set(env_allowlist)
        self.require_confirmation = require_confirmation
        self.require_typed_confirmation_for_high_risk = require_typed_confirmation_for_high_risk
        self.bypass_confirmation_for_allowed_commands = bypass_confirmation_for_allowed_commands
        self.godmode = godmode

    def evaluate(self, command: str | list[str]) -> CommandDecision:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            return CommandDecision(False, [], "empty command")
        executable = Path(argv[0]).name
        high_risk = executable in HIGH_RISK_TOKENS
        if any(part in {"|", "&&", "||", ";", ">", ">>", "<"} for part in argv):
            if self.godmode and isinstance(command, str):
                return CommandDecision(True, ["bash", "-lc", command], "allowed by godmode", True, False, False, False)
            return CommandDecision(False, argv, "shell control operators are not allowed", True)
        if executable == "sudo":
            requires_confirmation = not self.godmode
            requires_typed_confirmation = self.require_typed_confirmation_for_high_risk and not self.godmode
            return CommandDecision(
                True,
                argv,
                "sudo requires elevated confirmation",
                True,
                requires_confirmation,
                requires_typed_confirmation,
                True,
            )
        if executable in self.denied and not self.godmode:
            return CommandDecision(False, argv, f"{executable} is denied", high_risk)
        if executable not in self.allowed and not self.godmode:
            return CommandDecision(False, argv, f"{executable} is not in the allowlist", high_risk)
        requires_confirmation = self.require_confirmation
        requires_typed_confirmation = high_risk and self.require_typed_confirmation_for_high_risk
        if self.godmode:
            requires_confirmation = False
            requires_typed_confirmation = False
        elif self.bypass_confirmation_for_allowed_commands and executable in self.allowed and not high_risk:
            requires_confirmation = False
        return CommandDecision(
            True,
            argv,
            "allowed by godmode" if self.godmode else "allowed by policy",
            high_risk,
            requires_confirmation,
            requires_typed_confirmation,
            False,
        )

    def filtered_env(self) -> dict[str, str]:
        return {key: value for key, value in os.environ.items() if key in self.env_allowlist}


class CommandRunner:
    def __init__(self, policy: CommandPolicy, audit_logger: logging.Logger, timeout_seconds: float, max_output_chars: int) -> None:
        self.policy = policy
        self.audit_logger = audit_logger
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def run(self, argv: list[str], sudo_password: str | None = None) -> dict:
        self.audit_logger.info("command_execute", extra={"extra": {"argv": argv, "cwd": str(self.policy.working_directory)}})
        input_text = None
        run_argv = list(argv)
        if Path(run_argv[0]).name == "sudo" and sudo_password is not None:
            run_argv = ["sudo", "-S", "-p", "", *run_argv[1:]]
            input_text = sudo_password + "\n"
        try:
            result = subprocess.run(
                run_argv,
                cwd=self.policy.working_directory,
                env=self.policy.filtered_env(),
                text=True,
                input=input_text,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
            stdout = result.stdout[: self.max_output_chars]
            stderr = result.stderr[: self.max_output_chars]
            payload = {"returncode": result.returncode, "stdout": stdout, "stderr": stderr}
            self.audit_logger.info("command_result", extra={"extra": {"argv": argv, "returncode": result.returncode}})
            return payload
        except subprocess.TimeoutExpired as exc:
            self.audit_logger.warning("command_timeout", extra={"extra": {"argv": argv}})
            return {
                "returncode": 124,
                "stdout": _decode_output(exc.stdout),
                "stderr": _decode_output(exc.stderr) or "command timed out",
            }
        except FileNotFoundError:
            executable = Path(argv[0]).name
            self.audit_logger.warning("command_not_found", extra={"extra": {"argv": argv}})
            return {
                "returncode": 127,
                "stdout": "",
                "stderr": (
                    f"command not found: {executable}\n"
                    "If this tool is required for the authorized task, install it with the system package manager "
                    "or use an available equivalent and continue."
                ),
            }


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
