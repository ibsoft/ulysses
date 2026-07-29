from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .assessment import assessment_checks


@dataclass(frozen=True)
class Artifact:
    path: Path
    chars: int


@dataclass(frozen=True)
class AssessmentProject:
    path: Path
    scripts_dir: Path
    artifacts_dir: Path
    results_dir: Path
    reports_dir: Path


class ArtifactManager:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir.expanduser().resolve()
        self.attachments_dir = self.runtime_dir / "attachments"
        self.reports_dir = self.runtime_dir / "reports"
        self.projects_dir = self.runtime_dir / "projects"

    @classmethod
    def from_config(cls, config) -> "ArtifactManager":
        runtime_dir = Path(config.logging.directory).expanduser().parent
        return cls(runtime_dir)

    def save_text_attachment(self, session_id: str, text: str) -> Artifact:
        return self._write("attachments", session_id, "paste", "txt", text)

    def save_markdown_report(self, session_id: str, markdown: str) -> Artifact:
        return self._write("reports", session_id, "report", "md", markdown)

    def create_assessment_project(self, session_id: str, request: str) -> AssessmentProject:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        project_name = f"{_safe_filename(session_id)}_{timestamp}_{_safe_filename(request)[:48]}"
        project_dir = self.projects_dir / project_name
        scripts_dir = project_dir / "scripts"
        artifacts_dir = project_dir / "artifacts"
        results_dir = project_dir / "results"
        reports_dir = project_dir / "reports"
        for directory in (scripts_dir, artifacts_dir, results_dir, reports_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (project_dir / "README.md").write_text(_project_readme(request), encoding="utf-8")
        (artifacts_dir / "request.txt").write_text(request, encoding="utf-8")
        (artifacts_dir / "project.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "request": request,
                    "created_at": datetime.now().astimezone().isoformat(),
                    "status": "active",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return AssessmentProject(project_dir, scripts_dir, artifacts_dir, results_dir, reports_dir)

    def save_project_result(self, project: AssessmentProject, label: str, content: str) -> Artifact:
        return self._write_in(project.results_dir, label, "txt", content)

    def save_project_script(self, project: AssessmentProject, label: str, content: str) -> Artifact:
        return self._write_in(project.scripts_dir, label, "py", content)

    def save_project_markdown_report(self, project: AssessmentProject, markdown: str) -> Artifact:
        return self._write_in(project.reports_dir, "customer-vulnerability-assessment-report", "md", markdown)

    def list_downloads(self, limit: int = 20) -> list[Path]:
        files = (
            list(self.attachments_dir.glob("*.txt"))
            + list(self.reports_dir.glob("*.md"))
            + list(self.projects_dir.glob("*/reports/*.md"))
        )
        return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]

    def _write(self, directory_name: str, session_id: str, label: str, suffix: str, content: str) -> Artifact:
        directory = self.runtime_dir / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        filename = f"{_safe_filename(session_id)}_{timestamp}_{label}.{suffix}"
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        return Artifact(path=path, chars=len(content))

    def _write_in(self, directory: Path, label: str, suffix: str, content: str) -> Artifact:
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = directory / f"{timestamp}_{_safe_filename(label)}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return Artifact(path=path, chars=len(content))


def should_store_large_paste(text: str, context_max_chars: int) -> bool:
    threshold = max(8_000, min(24_000, context_max_chars // 2))
    return len(text) > threshold


def attachment_prompt(original_text: str, artifact: Artifact, preview_chars: int = 4_000) -> str:
    preview = original_text[:preview_chars].strip()
    omitted = max(0, len(original_text) - len(preview))
    return (
        "The user pasted a large text attachment.\n"
        f"Saved file: {artifact.path}\n"
        f"Characters: {artifact.chars}\n"
        f"Preview:\n{preview}\n"
        f"\n[The remaining {omitted} characters are saved in the file above.]"
    )


def is_report_request(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"\b(report|write[- ]?up|briefing|markdown file|download|downloadable)\b|\.md\b", lowered)
        and re.search(r"\b(make|create|write|generate|prepare|give)\b", lowered)
    )


def is_assessment_request(text: str) -> bool:
    lowered = text.lower()
    assessment_terms = r"\b(assessment|vulnerability test|vulnerability scan|vuln scan|pentest|pen test|penetration test|security scan|security test|port scan|scan|nikto|nmap|whatweb|nuclei)\b"
    action_terms = r"\b(make|create|write|generate|prepare|give|do|run|conduct|start|perform)\b"
    target_terms = r"\b(on|against|for)\b|\b(?:https?://|www\.|[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
    return bool(re.search(assessment_terms, lowered) and re.search(action_terms, lowered) and re.search(target_terms, text))


def is_assessment_continuation(text: str) -> bool:
    lowered = text.lower()
    continuation_terms = (
        "continue",
        "installed",
        "check this",
        "test this",
        "scan this",
        "use this",
        "try this",
        "proceed",
        "go on",
        "next",
        "finish",
        "complete",
    )
    return bool(any(term in lowered for term in continuation_terms) or re.search(r"\bhttps?://|\bwww\.", text))


def is_final_assessment_report(text: str) -> bool:
    lowered = text.lower()
    blocking_terms = (
        "please confirm",
        "would you like",
        "provide sample",
        "please advise",
        "cannot proceed",
        "i can proceed",
        "i will continue",
        "confirm or provide",
    )
    if any(term in lowered for term in blocking_terms):
        return False
    required = ("executive summary", "methodology")
    evidence = ("findings", "evidence")
    closure = ("remediation", "recommendations", "verification steps", "limitations")
    return bool(all(term in lowered for term in required) and any(term in lowered for term in evidence) and any(term in lowered for term in closure))


def assessment_command_for_text(text: str, project_request: str) -> str | None:
    lowered = text.lower()
    target = assessment_target(text) or assessment_target(project_request)

    if target and "nikto" in lowered and any(term in lowered for term in ("run", "scan", "check", "test")):
        scan_target = target if target.startswith(("http://", "https://")) else f"https://{target}"
        return f"nikto -host {scan_target} -nointeractive"

    if target and "whatweb" in lowered and any(term in lowered for term in ("run", "scan", "check", "test")):
        scan_target = target if target.startswith(("http://", "https://")) else f"https://{target}"
        return f"whatweb --no-errors {scan_target}"

    if target and "nmap" in lowered and "sudo" not in lowered and any(term in lowered for term in ("run", "scan", "check", "test")):
        return f"nmap -sT -Pn --top-ports 1000 {target.removeprefix('https://').removeprefix('http://').split('/', 1)[0]}"

    install = re.search(r"\binstall\s+([A-Za-z0-9_.+-]+)\b", lowered)
    if install:
        package = install.group(1)
        if package not in {"it", "tool", "tools", "package", "packages"}:
            return f"sudo apt-get install -y {package}"

    wants_sudo_scan = "sudo" in lowered and any(term in lowered for term in ("run", "scan", "nmap", "privileged", "syn"))
    if wants_sudo_scan:
        if target:
            return f"sudo nmap -sS -Pn -p- {target}"
    return None


def assessment_target(text: str) -> str | None:
    match = re.search(r"https?://([^/\s]+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip(".,;:")
    match = re.search(r"\b(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    if match:
        return match.group(0).strip(".,;:")
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    if match and all(0 <= int(part) <= 255 for part in match.group(0).split(".")):
        return match.group(0)
    return None


def assessment_baseline_commands(target: str) -> list[str]:
    return [check.command for check in assessment_checks(target)]


def assessment_needs_voice(text: str, pending_tool: bool = False) -> bool:
    if pending_tool:
        return False
    lowered = text.lower()
    important_terms = (
        "confirmation token",
        "requires confirmation",
        "please confirm",
        "confirm that",
        "authorization",
        "scope",
        "install",
        "not in the allowlist",
        "not available",
        "command not found",
        "cannot proceed",
        "blocked",
    )
    return any(term in lowered for term in important_terms)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned[:80] or "ulysses"


def _project_readme(request: str) -> str:
    return (
        "# Assessment Project\n\n"
        "Folders:\n"
        "- `scripts/`: purpose-built helper scripts for this assessment.\n"
        "- `artifacts/`: scope, pasted inputs, screenshots, payload lists, and supporting files.\n"
        "- `results/`: raw command output and intermediate evidence.\n"
        "- `reports/`: final Markdown report.\n\n"
        f"Initial request:\n\n{request}\n"
    )
