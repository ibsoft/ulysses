from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

SECRET_KEYS = ("api_key", "authorization", "credential", "oauth", "token", "password", "secret")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SECRET_KEYS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > 20 and value.startswith(("sk-", "Bearer ")):
        return "<redacted>"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        if hasattr(record, "extra"):
            payload["extra"] = redact(getattr(record, "extra"))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(directory: Path, level: str = "INFO", max_bytes: int = 2_000_000, backups: int = 5) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    handler = RotatingFileHandler(directory / "ulysses.jsonl", maxBytes=max_bytes, backupCount=backups)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def audit_logger(directory: Path) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ulysses.security.audit")
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(directory / "security_audit.jsonl", maxBytes=2_000_000, backupCount=5)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
