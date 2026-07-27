from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Artifact:
    path: Path
    chars: int


class ArtifactManager:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir.expanduser().resolve()
        self.attachments_dir = self.runtime_dir / "attachments"
        self.reports_dir = self.runtime_dir / "reports"

    @classmethod
    def from_config(cls, config) -> "ArtifactManager":
        runtime_dir = Path(config.logging.directory).expanduser().parent
        return cls(runtime_dir)

    def save_text_attachment(self, session_id: str, text: str) -> Artifact:
        return self._write("attachments", session_id, "paste", "txt", text)

    def save_markdown_report(self, session_id: str, markdown: str) -> Artifact:
        return self._write("reports", session_id, "report", "md", markdown)

    def list_downloads(self, limit: int = 20) -> list[Path]:
        files = list(self.attachments_dir.glob("*.txt")) + list(self.reports_dir.glob("*.md"))
        return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]

    def _write(self, directory_name: str, session_id: str, label: str, suffix: str, content: str) -> Artifact:
        directory = self.runtime_dir / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        filename = f"{_safe_filename(session_id)}_{timestamp}_{label}.{suffix}"
        path = directory / filename
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


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned[:80] or "ulysses"
