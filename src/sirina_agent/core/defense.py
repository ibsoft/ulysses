from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import Callable


@dataclass(frozen=True)
class DefenseCheck:
    name: str
    command: str
    purpose: str


@dataclass
class DefenseFinding:
    severity: str
    title: str
    evidence: str
    score: int
    attacker_ip: str | None = None


@dataclass(frozen=True)
class DefenseAction:
    name: str
    command: str
    reason: str


@dataclass
class DefenseAssessment:
    checked_at: str
    commands: list[DefenseCheck]
    outputs: list[dict] = field(default_factory=list)
    findings: list[DefenseFinding] = field(default_factory=list)
    planned_actions: list[DefenseAction] = field(default_factory=list)
    action_outputs: list[dict] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(finding.score for finding in self.findings)

    @property
    def highest_severity(self) -> str:
        order = {"Critical": 5, "High": 4, "Medium": 3, "Low": 2, "Informational": 1}
        if not self.findings:
            return "Informational"
        return max(self.findings, key=lambda finding: order.get(finding.severity, 0)).severity

    def prompt_text(self) -> str:
        output_blocks = []
        for output in self.outputs:
            output_blocks.append(
                f"## {output['name']}\n"
                f"Purpose: {output['purpose']}\n"
                f"Command: `{output['command']}`\n"
                f"OK: {output['ok']}\n"
                f"Output:\n```text\n{output['content']}\n```"
            )
        finding_blocks = [
            f"- {finding.severity}: {finding.title}\n  Evidence: {finding.evidence}"
            for finding in self.findings
        ]
        action_blocks = [
            f"- {action.name}: `{action.command}`\n  Reason: {action.reason}"
            for action in self.planned_actions
        ]
        action_output_blocks = [
            f"- {output['name']}: ok={output['ok']}\n  Command: `{output['command']}`\n  Output: {output['content'][:1000]}"
            for output in self.action_outputs
        ]
        return (
            f"Autonomous defensive host assessment at {self.checked_at}\n"
            f"Risk score: {self.score}\n"
            f"Highest severity: {self.highest_severity}\n\n"
            f"Evidence findings:\n{chr(10).join(finding_blocks) if finding_blocks else '- No obvious compromise indicators detected.'}\n\n"
            f"Planned defensive actions:\n{chr(10).join(action_blocks) if action_blocks else '- None.'}\n\n"
            f"Executed defensive action outputs:\n{chr(10).join(action_output_blocks) if action_output_blocks else '- None.'}\n\n"
            f"Command outputs:\n{chr(10).join(output_blocks)}"
        )


class AutonomousDefenseEngine:
    CHECKS = [
        DefenseCheck("kernel", "uname -a", "baseline kernel and host platform"),
        DefenseCheck("uptime", "uptime", "load average and uptime baseline"),
        DefenseCheck("disk", "df -h", "filesystem capacity and pressure"),
        DefenseCheck("sessions", "who", "currently logged-in users"),
        DefenseCheck("login_history", "last -n 12", "recent login history"),
        DefenseCheck("listeners", "ss -tulpn", "listening network services"),
        DefenseCheck("processes", "ps aux", "running process review"),
        DefenseCheck("network", "ip addr show", "local network interfaces"),
        DefenseCheck("journal_warnings", "journalctl -p warning -n 40 --no-pager", "recent system warnings"),
        DefenseCheck("ufw_present", "which ufw", "firewall management availability"),
        DefenseCheck("fail2ban_present", "which fail2ban-client", "brute-force protection availability"),
        DefenseCheck("auditd_present", "which auditctl", "audit subsystem availability"),
    ]

    SUSPICIOUS_PORTS = {"1337", "2323", "31337", "4444", "5555", "6666", "6667", "9001"}
    SUSPICIOUS_PROCESS_PATTERNS = [
        r"\bnc\s+-l\b",
        r"\bncat\s+-l\b",
        r"\bsocat\b.*\bexec\b",
        r"\bbash\s+-i\b",
        r"\bsh\s+-i\b",
        r"meterpreter",
        r"reverse[_ -]?shell",
        r"python\d*(\.\d+)?\s+-m\s+http\.server",
    ]

    def run(self, run_command: Callable[[DefenseCheck], tuple[bool, str]]) -> DefenseAssessment:
        assessment = DefenseAssessment(checked_at=datetime.now(UTC).isoformat(), commands=list(self.CHECKS))
        for check in self.CHECKS:
            ok, content = run_command(check)
            assessment.outputs.append(
                {"name": check.name, "command": check.command, "purpose": check.purpose, "ok": ok, "content": content}
            )
        assessment.findings.extend(self._findings(assessment.outputs))
        return assessment

    def plan_actions(
        self,
        assessment: DefenseAssessment,
        auto_block_attackers: bool,
        install_missing_security_apps: bool,
    ) -> list[DefenseAction]:
        actions: list[DefenseAction] = []
        if install_missing_security_apps:
            missing = self._missing_security_apps(assessment.outputs)
            if missing:
                package_names = " ".join(missing)
                actions.append(
                    DefenseAction(
                        "install_security_apps",
                        f"sudo apt-get install -y {package_names}",
                        "install missing local defensive tooling",
                    )
                )
        if auto_block_attackers:
            for ip in sorted({finding.attacker_ip for finding in assessment.findings if finding.attacker_ip}):
                actions.append(DefenseAction("block_attacker", f"sudo ufw deny from {ip}", f"block hostile source {ip}"))
        assessment.planned_actions = actions
        return actions

    def _findings(self, outputs: list[dict]) -> list[DefenseFinding]:
        findings: list[DefenseFinding] = []
        by_name = {output["name"]: output for output in outputs}
        findings.extend(self._disk_findings(by_name.get("disk", {}).get("content", "")))
        findings.extend(self._listener_findings(by_name.get("listeners", {}).get("content", "")))
        findings.extend(self._process_findings(by_name.get("processes", {}).get("content", "")))
        findings.extend(self._journal_findings(by_name.get("journal_warnings", {}).get("content", "")))
        findings.extend(self._missing_app_findings(outputs))
        findings.extend(self._failed_check_findings(outputs))
        return findings

    def _disk_findings(self, text: str) -> list[DefenseFinding]:
        findings = []
        for line in text.splitlines():
            match = re.search(r"\s(\d{1,3})%\s+(/\S*)$", line)
            if not match:
                continue
            percent = int(match.group(1))
            mount = match.group(2)
            if percent >= 95:
                findings.append(DefenseFinding("High", f"filesystem critically full: {mount}", line, 4))
            elif percent >= 85:
                findings.append(DefenseFinding("Medium", f"filesystem nearing capacity: {mount}", line, 2))
        return findings

    def _listener_findings(self, text: str) -> list[DefenseFinding]:
        findings = []
        for line in text.splitlines():
            if "LISTEN" not in line:
                continue
            for port in self.SUSPICIOUS_PORTS:
                if re.search(rf":{port}\b", line):
                    findings.append(DefenseFinding("High", f"suspicious listening port {port}", line, 4))
        return findings

    def _process_findings(self, text: str) -> list[DefenseFinding]:
        findings = []
        for line in text.splitlines():
            lowered = line.lower()
            for pattern in self.SUSPICIOUS_PROCESS_PATTERNS:
                if re.search(pattern, lowered):
                    findings.append(DefenseFinding("High", "suspicious process pattern", line[:500], 4))
                    break
        return findings

    def _journal_findings(self, text: str) -> list[DefenseFinding]:
        lowered = text.lower()
        failed_auth = lowered.count("failed password") + lowered.count("authentication failure")
        findings = []
        brute_force_sources = self._failed_auth_sources(text)
        for ip, count in sorted(brute_force_sources.items(), key=lambda item: item[1], reverse=True):
            if count >= 8:
                findings.append(DefenseFinding("High", f"brute-force source detected: {ip}", f"{count} failed auth events", 4, ip))
            elif count >= 3:
                findings.append(DefenseFinding("Medium", f"possible brute-force source: {ip}", f"{count} failed auth events", 2, ip))
        port_scan_sources = self._port_scan_sources(text)
        for ip, ports in sorted(port_scan_sources.items(), key=lambda item: len(item[1]), reverse=True):
            if len(ports) >= 8:
                findings.append(DefenseFinding("High", f"port-scan source detected: {ip}", f"destination ports: {', '.join(sorted(ports))}", 4, ip))
            elif len(ports) >= 4:
                findings.append(DefenseFinding("Medium", f"possible port-scan source: {ip}", f"destination ports: {', '.join(sorted(ports))}", 2, ip))
        if failed_auth >= 10 and not brute_force_sources:
            findings.append(DefenseFinding("High", "many recent authentication failures", f"{failed_auth} failures", 4))
        elif failed_auth >= 3 and not brute_force_sources:
            findings.append(DefenseFinding("Medium", "recent authentication failures", f"{failed_auth} failures", 2))
        return findings

    def _failed_auth_sources(self, text: str) -> dict[str, int]:
        sources: dict[str, int] = {}
        for line in text.splitlines():
            lowered = line.lower()
            if "failed password" not in lowered and "authentication failure" not in lowered:
                continue
            for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line):
                sources[ip] = sources.get(ip, 0) + 1
        return sources

    def _port_scan_sources(self, text: str) -> dict[str, set[str]]:
        sources: dict[str, set[str]] = {}
        for line in text.splitlines():
            src = re.search(r"\bSRC=((?:\d{1,3}\.){3}\d{1,3})\b", line)
            dpt = re.search(r"\bDPT=(\d{1,5})\b", line)
            if not src or not dpt:
                continue
            ip = src.group(1)
            sources.setdefault(ip, set()).add(dpt.group(1))
        return sources

    def _missing_security_apps(self, outputs: list[dict]) -> list[str]:
        checks = {"ufw_present": "ufw", "fail2ban_present": "fail2ban", "auditd_present": "auditd"}
        by_name = {output["name"]: output for output in outputs}
        return [package for check_name, package in checks.items() if check_name in by_name and not by_name[check_name]["ok"]]

    def _missing_app_findings(self, outputs: list[dict]) -> list[DefenseFinding]:
        return [
            DefenseFinding("Low", f"missing security app: {package}", "not found in PATH", 1)
            for package in self._missing_security_apps(outputs)
        ]

    def _failed_check_findings(self, outputs: list[dict]) -> list[DefenseFinding]:
        return [
            DefenseFinding("Low", f"defense check failed: {output['name']}", output["content"][:500], 1)
            for output in outputs
            if not output["ok"]
        ]
