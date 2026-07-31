from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class UpdateStatus:
    state: Literal["available", "current", "unknown", "checking", "installing", "staged"]
    installed_commit: str = ""
    latest_commit: str = ""
    latest_branch: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.state == "available":
            release = f" {self.latest_branch}" if self.latest_branch else ""
            return f"available{release} ({self.installed_commit[:8]} -> {self.latest_commit[:8]})"
        if self.state == "current":
            release = f" {self.latest_branch}" if self.latest_branch else ""
            return f"up to date{release} ({self.installed_commit[:8]})"
        if self.state in {"checking", "installing", "staged"}:
            return self.state
        return "unknown"


class UpdateManager:
    def __init__(self, config) -> None:
        self.config = config
        metadata = self._metadata()
        self.installed_branch = str(metadata.get("source_branch") or "")
        self.status = UpdateStatus("unknown", latest_branch=self.installed_branch)

    def _metadata(self) -> dict[str, str]:
        path = Path(self.config.metadata_path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def check(self) -> UpdateStatus:
        known_branch = self.status.latest_branch
        self.status = UpdateStatus("checking", latest_branch=known_branch)
        metadata = self._metadata()
        installed = str(metadata.get("main_commit") or metadata.get("source_commit") or "").strip()
        if not installed:
            self.status = UpdateStatus(
                "unknown",
                latest_branch=known_branch,
                error="Installed build metadata is unavailable; reinstall once to initialize it.",
            )
            return self.status
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", self.config.repository_url],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.status = UpdateStatus(
                "unknown",
                installed_commit=installed,
                latest_branch=known_branch,
                error=f"Update check failed: {exc}",
            )
            return self.status
        refs: dict[str, str] = {}
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                fields = line.split(maxsplit=1)
                if len(fields) == 2 and fields[1].startswith("refs/heads/"):
                    refs[fields[1].removeprefix("refs/heads/")] = fields[0]
        latest = refs.get(self.config.branch, "")
        if not latest:
            detail = result.stderr.strip() or "main branch was not returned by the remote"
            self.status = UpdateStatus(
                "unknown",
                installed_commit=installed,
                latest_branch=known_branch,
                error=f"Update check failed: {detail}",
            )
            return self.status
        version_branches = [branch for branch in refs if re.match(r"^v?[_-]?\d", branch)]
        latest_branch = max(version_branches, key=_natural_version_key, default=self.config.branch)
        state = "current" if installed == latest else "available"
        self.status = UpdateStatus(
            state,
            installed_commit=installed,
            latest_commit=latest,
            latest_branch=latest_branch,
        )
        return self.status

    def install(self) -> str:
        if self.status.state != "available":
            checked = self.check()
            if checked.state != "available":
                return checked.error or f"No update was staged: {checked.summary()}."
        release_branch = self.status.latest_branch
        self.status = UpdateStatus("installing", latest_branch=release_branch)
        script = Path(self.config.updater_path)
        if not script.is_file():
            self.status = UpdateStatus(
                "unknown", latest_branch=release_branch, error=f"Updater is unavailable: {script}"
            )
            return self.status.error
        try:
            result = subprocess.run(
                [str(script), self.config.repository_url, self.config.branch],
                capture_output=True,
                text=True,
                timeout=self.config.install_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.status = UpdateStatus("unknown", latest_branch=release_branch, error=f"Update failed: {exc}")
            return self.status.error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            self.status = UpdateStatus(
                "unknown", latest_branch=release_branch, error=f"Update failed: {detail[-2000:]}"
            )
            return self.status.error
        self.status = UpdateStatus("staged", latest_branch=release_branch)
        return (
            "Update downloaded and staged safely. Exit Ulysses, then run `ulysses` again. "
            "The launcher will apply the update with the active configuration preserved before opening any database."
        )


def _natural_version_key(value: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value))
