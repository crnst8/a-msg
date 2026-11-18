"""Structured JSON logs with secrets removed. Message bodies are never logged."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

TOKEN = re.compile(r"amsg_[0-9a-f-]{36}\.[A-Za-z0-9_-]+")
BEARER = re.compile(r"(?i)bearer\s+\S+")
RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


def redact(value: str) -> str:
    return BEARER.sub("Bearer [redacted]", TOKEN.sub("[redacted-token]", value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in RESERVED:
                entry[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure(log_dir: Path, *, level: str = "INFO", console: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    formatter = JsonFormatter()
    file_handler = logging.handlers.RotatingFileHandler(log_dir / "service.log", maxBytes=10_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)
    logging.getLogger("uvicorn.access").disabled = True
